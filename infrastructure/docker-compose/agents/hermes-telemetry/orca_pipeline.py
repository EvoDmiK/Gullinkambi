"""Manifest/result files + stored call events → attributed events → attempt snapshots.

Read-only on the source logs. Writes only through the SECURITY DEFINER functions from
migrations 001-003. Re-running with the same inputs is a no-op (snapshot fingerprints).
Connection string comes from ORCA_PIPELINE_DSN, never from argv.
"""
from __future__ import annotations

import collections
import json
import os
import sys
from pathlib import Path
from typing import Any, Iterable

import orca_attribution
import orca_calls
import orca_snapshot

CALL_COLUMNS = (
    "event_key", "agent", "normalization_version", "event_status", "session_id_hash", "source_request_id_hash", "occurred_at",
    "project_key", "task_id", "attempt_id", *orca_snapshot.METRIC_FIELDS,
    "tool_calls", "tool_calls_total", "tool_failures",
)


def _documents(path: Path) -> Iterable[Any]:
    for file in sorted(path.glob("*.json*")):
        text = file.read_text(encoding="utf-8")
        if file.suffix == ".jsonl":
            for line in filter(None, (l.strip() for l in text.splitlines())):
                yield json.loads(line)
        elif file.suffix == ".json":
            yield json.loads(text)


def _validated(manifest_docs: Iterable[Any], result_docs: Iterable[Any], allow_fixture: bool,
               verifier_key: str | None = None) -> dict[str, Any]:
    """Validate every document; invalid ones are counted by code, never echoed."""
    manifests, results = [], []
    rejected: dict[str, int] = {}

    def reject(code: str):
        rejected[code] = rejected.get(code, 0) + 1

    for doc in manifest_docs:
        try:
            manifests.append(orca_attribution.validate_manifest(doc))
        except orca_attribution.InvalidInput as exc:
            reject(f"manifest:{str(exc).split(':')[0]}")
    for doc in result_docs:
        try:
            result = orca_attribution.validate_result(doc)
        except orca_attribution.InvalidInput as exc:
            reject(f"result:{str(exc).split(':')[0]}")
            continue
        if result["evidence_kind"] == "fixture" and not allow_fixture:
            reject("fixture_result_not_allowed")
            continue
        if verifier_key and result["producer_type"] == "verifier" and not orca_attribution.signature_valid(result, verifier_key):
            # Dropped before the "latest per stream" choice, so a forged verifier result with a higher
            # revision cannot displace a genuine one.
            reject("result:verifier_signature_invalid")
            continue
        results.append(result)
    return {"manifests": orca_attribution.latest_manifests(manifests),
            "results": _latest_results(results), "rejected": rejected}


def load_inputs(directory: Path, allow_fixture: bool = False, verifier_key: str | None = None) -> dict[str, Any]:
    """Read manifests/ and results/ JSON files from a directory."""
    docs: dict[str, list[Any]] = {"manifests": [], "results": []}
    rejected: dict[str, int] = {}
    for kind in docs:
        base = directory / kind
        if not base.is_dir():
            continue
        try:
            docs[kind] = list(_documents(base))
        except (json.JSONDecodeError, UnicodeDecodeError):
            rejected[f"{kind[:-1]}_unreadable"] = 1
    loaded = _validated(docs["manifests"], docs["results"], allow_fixture, verifier_key)
    for code, count in rejected.items():
        loaded["rejected"][code] = loaded["rejected"].get(code, 0) + count
    return loaded


def load_inputs_from_db(conn, allow_fixture: bool = False, verifier_key: str | None = None) -> dict[str, Any]:
    """Read the manifests/results that orca_task.py delivered through the webhook.

    All rows are read on purpose: revisions are append-only, and dropping old manifests would
    release their calls and rebuild those attempts' snapshots with no calls.
    """
    manifests = [row[0] for row in conn.execute(
        "SELECT manifest FROM observability.orca_task_manifests ORDER BY task_id, attempt_id, revision")]
    results = [row[0] for row in conn.execute(
        "SELECT result FROM observability.orca_task_results ORDER BY task_id, attempt_id, result_revision")]
    return _validated(manifests, results, allow_fixture, verifier_key)


def _latest_results(results: Iterable[dict[str, Any]]) -> dict[tuple[str, str], dict[str, dict[str, Any]]]:
    """Latest result per attempt, kept separately for the agent's report and the verifier's evidence."""
    best: dict[tuple[str, str], dict[str, dict[str, Any]]] = {}
    for r in results:
        stream = "verify" if r["producer_type"] == "verifier" else "report"
        slot = best.setdefault((r["task_id"], r["attempt_id"]), {})
        if stream not in slot or r["result_revision"] > slot[stream]["result_revision"]:
            slot[stream] = r
    return best


AUTO_LOOKBACK_DAYS = 14   # sessions older than this are not grouped automatically
AUTO_ATTEMPT = "a1"


def _fetch_calls(conn, sessions: set[str], requests: set[str], auto_sessions: bool = False) -> list[dict[str, Any]]:
    columns = ", ".join(CALL_COLUMNS)
    recent = f"OR occurred_at > now() - interval '{AUTO_LOOKBACK_DAYS} days'" if auto_sessions else ""
    cursor = conn.execute(
        f"""SELECT {columns} FROM observability.orca_call_events
            WHERE source_kind = 'raw_call'
              AND (session_id_hash = ANY(%s) OR source_request_id_hash = ANY(%s)
                   OR task_id IS NOT NULL {recent})""",  # keep attributed rows so corrections can release them
        (sorted(sessions), sorted(requests)))
    return [dict(zip(CALL_COLUMNS, row)) for row in cursor.fetchall()]


def _auto_group(events: list[dict[str, Any]], assignments: list[dict[str, Any]]) -> dict[tuple[str, str], str]:
    """Assign still-unassigned calls to one automatic attempt per (agent, session).

    Explicit manifests were applied first, so they always win; `ambiguous` calls are left alone.
    Returns {(task_id, attempt_id): project label}.
    """
    groups: dict[tuple[str, str], list[int]] = {}
    for index, (event, verdict) in enumerate(zip(events, assignments)):
        if verdict["attribution_status"] == "unassigned":
            groups.setdefault((event["agent"], event["session_id_hash"]), []).append(index)
    projects: dict[tuple[str, str], str] = {}
    for (agent, session), indexes in groups.items():
        task_id = f"auto-{agent}-{session[:8]}"
        for index in indexes:
            assignments[index].update(task_id=task_id, attempt_id=AUTO_ATTEMPT,
                                      attribution_method="auto_session", attribution_status="auto")
        common = collections.Counter(events[i]["project_key"] for i in indexes).most_common(1)[0][0]
        projects[(task_id, AUTO_ATTEMPT)] = f"p-{common[:12]}"
    return projects


def run_pipeline(conn, inputs: dict[str, Any], approved_verifiers: Iterable[str],
                 verifier_key: str | None = None, auto_sessions: bool = False,
                 auto_throttle_seconds: int = 600) -> dict[str, Any]:
    manifests, results = inputs["manifests"], inputs["results"]
    sessions = {m["session_hash"] for m in manifests if "window" in m}
    requests = {h for m in manifests if "request_hashes" in m for h in m["request_hashes"]}
    events = _fetch_calls(conn, sessions, requests, auto_sessions)

    assignments, previous = [], {}
    for event in events:
        verdict = orca_attribution.attribute(event, manifests, orca_calls._hash)
        previous[event["event_key"]] = (event["task_id"], event["attempt_id"])
        assignments.append({"event_key": event["event_key"], **verdict})
    auto_projects = _auto_group(events, assignments) if auto_sessions else {}
    (changed,) = conn.execute(
        "SELECT observability.assign_orca_call_attributions(%s::jsonb)", (json.dumps(assignments),)).fetchone()

    # Attempts touched by old or new attribution, plus every attempt with a manifest.
    affected = {(m["task_id"], m["attempt_id"]): m["project_key"] for m in manifests}
    affected.update(auto_projects)
    by_attempt: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for event, verdict in zip(events, assignments):
        if verdict["task_id"]:
            by_attempt.setdefault((verdict["task_id"], verdict["attempt_id"]), []).append(event)
    for key in previous.values():
        if key[0]:
            affected.setdefault(key, "unknown")

    # A busy session changes on every pass; one snapshot revision per throttle window is enough.
    recent: dict[tuple[str, str], float] = {}
    if auto_sessions and auto_throttle_seconds > 0:
        recent = {(t, a): float(age) for t, a, age in conn.execute(
            """SELECT task_id, attempt_id, extract(epoch FROM now() - max(created_at))
               FROM observability.orca_attempt_snapshots WHERE task_id LIKE 'auto-%' GROUP BY 1, 2""").fetchall()}

    counts = {"attributed_changed": changed, "snapshots_recorded": 0, "snapshots_unchanged": 0,
              "ambiguous": sum(a["attribution_status"] == "ambiguous" for a in assignments),
              "unassigned": sum(a["attribution_status"] == "unassigned" for a in assignments),
              "auto_sessions": len(auto_projects), "auto_events": sum(a["attribution_status"] == "auto" for a in assignments),
              "auto_throttled": 0, "rejected_inputs": inputs["rejected"]}
    for (task_id, attempt_id), project_key in sorted(affected.items()):
        is_auto = task_id.startswith("auto-") and auto_sessions
        if is_auto and recent.get((task_id, attempt_id), float("inf")) < auto_throttle_seconds:
            counts["auto_throttled"] += 1
            continue
        rows = [{**e, "source_kind": "raw_call"} for e in by_attempt.get((task_id, attempt_id), [])]
        streams = results.get((task_id, attempt_id), {})
        snapshot = orca_snapshot.build_snapshot(
            task_id, attempt_id, project_key, rows, streams.get("report"), approved_verifiers,
            verification_result=streams.get("verify"), verifier_key=verifier_key,
            origin="auto" if is_auto else None)
        (revision,) = conn.execute(
            "SELECT observability.record_orca_attempt_snapshot(%s::jsonb)", (json.dumps(snapshot),)).fetchone()
        counts["snapshots_recorded" if revision else "snapshots_unchanged"] += 1
    return counts


def main(argv: list[str]) -> int:
    import argparse
    import psycopg

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("inputs", type=Path, nargs="?", help="directory with manifests/ and results/ (omit with --from-db)")
    parser.add_argument("--from-db", action="store_true", help="read manifests/results delivered via the webhook")
    parser.add_argument("--auto-sessions", action="store_true", help="group unregistered calls into one attempt per session")
    parser.add_argument("--allow-fixture", action="store_true", help="isolated integration only")
    args = parser.parse_args(argv)
    dsn = os.getenv("ORCA_PIPELINE_DSN")
    if not dsn:
        print("orca_pipeline: ORCA_PIPELINE_DSN is required", file=sys.stderr)
        return 2
    approved = {v for v in os.getenv("ORCA_APPROVED_VERIFIERS", "").split(",") if v}
    if not args.from_db and args.inputs is None:
        parser.error("give an inputs directory or --from-db")
    with psycopg.connect(dsn, autocommit=True) as conn:
        key = os.getenv("ORCA_VERIFIER_KEY") or None
        loaded = (load_inputs_from_db(conn, args.allow_fixture, key) if args.from_db
                  else load_inputs(args.inputs, args.allow_fixture, key))
        counts = run_pipeline(conn, loaded, approved, key, auto_sessions=args.auto_sessions)
    print(json.dumps(counts, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
