"""Build one complete, privacy-safe snapshot per task attempt (pure function)."""
from __future__ import annotations

import collections
import hashlib
import json
from typing import Any, Iterable

import orca_attribution
import orca_calls

SNAPSHOT_SCHEMA_VERSION = 1
METRIC_FIELDS = (
    "uncached_input_tokens", "cache_read_tokens", "cache_write_tokens", "output_tokens",
    "reasoning_tokens_subset", "normalized_input_tokens", "processed_total_tokens", "non_cache_tokens",
)


def _fingerprint(snapshot: dict[str, Any]) -> str:
    body = {k: v for k, v in snapshot.items() if k not in ("fingerprint", "revision")}
    return hashlib.sha256(json.dumps(body, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


TOP_TOOLS = 30


def _coverage(known: int, total: int) -> str:
    return "unknown" if known == 0 else "complete" if known == total else "partial"


def _add_tool_use(rows, metrics: dict, availability: dict) -> None:
    """Tool totals count only when every call reports them; None means "not observed", never 0."""
    for field in ("tool_calls_total", "tool_failures"):
        known = [r[field] for r in rows if r.get(field) is not None]
        availability[field] = _coverage(len(known), len(rows)) if rows else "unknown"
        if rows and len(known) == len(rows):
            metrics[field] = sum(known)


def _tool_breakdown(rows):
    """{tool name: calls}, top N plus "other"; None unless every call reports its tools."""
    if not rows or any(r.get("tool_calls") is None for r in rows):
        return None
    counts: collections.Counter = collections.Counter()
    for r in rows:
        counts.update(r["tool_calls"])
    top = dict(counts.most_common(TOP_TOOLS))
    rest = sum(counts.values()) - sum(top.values())
    if rest:
        top["other"] = top.get("other", 0) + rest
    return top


def build_snapshot(task_id: str, attempt_id: str, project_key: str,
                   calls: Iterable[dict[str, Any]], result: dict[str, Any] | None,
                   approved_verifiers: Iterable[str], verification_result: dict[str, Any] | None = None,
                   verifier_key: str | None = None, origin: str | None = None) -> dict[str, Any]:
    """`calls` are final canonical call rows already attributed to this attempt."""
    rows = [c for c in calls if c.get("source_kind", "raw_call") == "raw_call"]
    metrics: dict[str, int] = {}
    availability: dict[str, str] = {}
    for field in METRIC_FIELDS:
        known = [r[field] for r in rows if r.get(field) is not None]
        availability[field] = (
            "unknown" if not known else "complete" if len(known) == len(rows) else "partial"
        )
        if known and len(known) == len(rows):
            metrics[field] = sum(known)  # only complete sums become metrics
    _add_tool_use(rows, metrics, availability)
    agg = orca_calls.aggregate(rows)
    snapshot: dict[str, Any] = {
        "schema_version": SNAPSHOT_SCHEMA_VERSION,
        "normalization_version": orca_calls.NORMALIZATION_VERSION,
        "task_id": task_id, "attempt_id": attempt_id, "project_key": project_key,
        "agents": sorted({r["agent"] for r in rows}),
        "request_count": len(rows) if rows else None,  # no observed calls ≠ zero calls
        "partial_call_count": sum(1 for r in rows if r.get("event_status") != "final"),
        "metrics": metrics,
        "availability": availability,
        "cache_hit_rate_percent": agg["cache_hit_rate_percent"],
        "usage_coverage": agg["processed_total"]["coverage_state"] if rows else "unknown",
        "reported_status": "unknown", "verification_state": "unknown",
    }
    tools = _tool_breakdown(rows)
    if tools is not None:
        snapshot["tools"] = tools
    if origin:  # only set for automatic attempts, so registered snapshots keep their fingerprints
        snapshot["origin"] = origin
    # The agent's own report and the verifier's evidence are separate streams: a later `end`
    # from the agent must not erase a verification, and only the verifier's evidence counts.
    report = result if result is not None else verification_result
    if report is not None:
        evidence = verification_result if verification_result is not None else result
        snapshot.update(
            reported_status=report["reported_status"],
            verification_state=orca_attribution.effective_verification(evidence, approved_verifiers, verifier_key),
            result_revision=report["result_revision"],
            evidence_kind=evidence["evidence_kind"],
        )
        source = evidence if "test" in evidence else report
        if "test" in source:
            snapshot["test"] = source["test"]
        if "artifact_handle" in evidence:
            snapshot["verification_artifact"] = {
                "handle": evidence["artifact_handle"], "sha256": evidence["artifact_sha256"]}
    snapshot["fingerprint"] = _fingerprint(snapshot)
    return snapshot
