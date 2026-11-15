#!/usr/bin/env python3
"""Mark the start and end of an ORCA task attempt and send it through the telemetry webhook.

    orca_task.py begin --task TASK [--attempt a1] [--auto-session | --session SESSION_ID]
    orca_task.py end   --task TASK [--attempt a1] --status completed [--test-id unit --exit-code 0 ...]

`begin` records the attempt window; `end` closes it and reports the outcome. Calls made in
the same session inside the window are attributed to the attempt (see orca_attribution).
Session IDs are hashed here, so raw IDs never leave this machine. The reported status is
the agent's own claim: it only counts as verified when an approved verifier says so.
Webhook URL/token come from HERMES_TELEMETRY_WEBHOOK_URL / _TOKEN (environment or --env-file).
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
import orca_attribution  # noqa: E402
import orca_calls  # noqa: E402

PROVISIONAL_WINDOW = timedelta(hours=24)  # until `end` closes it; keeps a forgotten attempt from growing forever
RECENT_SESSION_SECONDS = 15 * 60
DEFAULT_STATE_DIR = Path.home() / ".local" / "state" / "orca-task"
USER_AGENT = "orca-task-cli/1"


def iso(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def detect_session(cwd: str, home: Path, now: float) -> str | None:
    """Newest recently-active Claude/Codex session whose working directory is `cwd`."""
    best: tuple[float, str] | None = None
    claude_dir = home / ".claude" / "projects" / "".join(c if c.isalnum() else "-" for c in cwd)
    if claude_dir.is_dir():
        for log in claude_dir.glob("*.jsonl"):
            mtime = log.stat().st_mtime
            if best is None or mtime > best[0]:
                best = (mtime, log.stem)
    sessions = home / ".codex" / "sessions"
    for day in (0, 1):  # today and yesterday cover a session that crosses midnight
        stamp = datetime.fromtimestamp(now - day * 86400)
        folder = sessions / f"{stamp:%Y}" / f"{stamp:%m}" / f"{stamp:%d}"
        for log in folder.glob("*.jsonl") if folder.is_dir() else []:
            mtime = log.stat().st_mtime
            if best is not None and mtime <= best[0]:
                continue
            try:
                with log.open(encoding="utf-8", errors="replace") as handle:
                    meta = json.loads(handle.readline())
            except (OSError, json.JSONDecodeError):
                continue
            payload = meta.get("payload") if isinstance(meta, dict) else None
            if isinstance(payload, dict) and payload.get("cwd") == cwd and payload.get("id"):
                best = (mtime, str(payload["id"]))
    if best is None or now - best[0] > RECENT_SESSION_SECONDS:
        return None
    return best[1]


def state_path(state_dir: Path, task: str, attempt: str) -> Path:
    return state_dir / f"{task}__{attempt}.json"


def manifest_doc(state: dict[str, Any], end: datetime, revision: int) -> dict[str, Any]:
    doc = {
        "schema_version": 1, "producer_id": state["producer"], "task_id": state["task"],
        "attempt_id": state["attempt"], "project_key": state["project"],
        "session_id_hash": state["session_hash"], "revision": revision,
        "window": {"start": state["start"], "end": iso(end)},
    }
    if state.get("role"):
        doc["role"] = state["role"]
    return doc


def result_doc(state: dict[str, Any], args, now: datetime, revision: int) -> dict[str, Any]:
    doc: dict[str, Any] = {
        "schema_version": 1, "event_id": f"res-{state['task']}-{state['attempt']}-{revision}",
        "task_id": state["task"], "attempt_id": state["attempt"], "result_revision": revision,
        "occurred_at": iso(now), "producer_id": state["producer"], "producer_type": "agent",
        "evidence_kind": "real", "reported_status": args.status, "verification_state": "unverified",
    }
    if args.test_id is not None:
        if args.exit_code is None:
            raise orca_attribution.InvalidInput("test_requires_exit_code")
        doc["test"] = {"test_id": args.test_id, "exit_code": args.exit_code}
        for key in ("passed", "failed", "skipped"):
            if getattr(args, key) is not None:
                doc["test"][key] = getattr(args, key)
    return doc


def read_env_file(path: Path | None) -> dict[str, str]:
    values: dict[str, str] = {}
    if path is None or not path.is_file():
        return values
    for line in path.read_text(encoding="utf-8").splitlines():
        key, sep, value = line.partition("=")
        if sep and key.strip() in ("HERMES_TELEMETRY_WEBHOOK_URL", "HERMES_TELEMETRY_WEBHOOK_TOKEN"):
            values[key.strip()] = value.strip().strip("'\"")
    return values


@lru_cache(maxsize=None)
def warn_insecure_transport(message: str) -> None:
    print(f"orca_task: warning: {message}", file=sys.stderr)


def post(url: str, token: str, manifests: list[dict], results: list[dict], now: datetime) -> None:
    if not token.strip():
        raise RuntimeError("HERMES_TELEMETRY_WEBHOOK_TOKEN is required")
    body = {"schema_version": 3, "collector_id": "orca-task-cli", "observed_at": iso(now)}
    if manifests:
        body["orca_task_manifests"] = manifests
    if results:
        body["orca_task_results"] = results
    headers = {"Content-Type": "application/json", "User-Agent": USER_AGENT, "X-Hermes-Telemetry-Schema": "3"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = urllib.request.Request(url, data=json.dumps(body).encode(), headers=headers, method="POST")
    with urllib.request.urlopen(request, timeout=30) as response:
        if not 200 <= response.status < 300:
            raise RuntimeError(f"webhook returned HTTP {response.status}")


def deliver(args, manifests: list[dict], results: list[dict], now: datetime) -> int:
    if args.dry_run:
        print(json.dumps({"orca_task_manifests": manifests, "orca_task_results": results}, indent=2, sort_keys=True))
        return 0
    env = {**read_env_file(args.env_file), **{k: v for k, v in os.environ.items() if k.startswith("HERMES_TELEMETRY_WEBHOOK")}}
    url = env.get("HERMES_TELEMETRY_WEBHOOK_URL")
    if not url:
        print("orca_task: HERMES_TELEMETRY_WEBHOOK_URL is required (or use --dry-run)", file=sys.stderr)
        return 2
    token = env.get("HERMES_TELEMETRY_WEBHOOK_TOKEN", "").strip()
    if not token:
        print("orca_task: HERMES_TELEMETRY_WEBHOOK_TOKEN is required", file=sys.stderr)
        return 2
    if url.lower().startswith("http://"):
        warn_insecure_transport("webhook uses HTTP")
    try:
        post(url, token, manifests, results, now)
    except (urllib.error.URLError, RuntimeError, OSError) as exc:
        print(f"orca_task: delivery failed: {type(exc).__name__}", file=sys.stderr)
        return 1
    return 0


def cmd_begin(args, now: datetime) -> int:
    attempt = args.attempt
    session = args.session or (detect_session(args.cwd, Path.home(), now.timestamp()) if args.auto_session else None)
    if not session:
        print("orca_task: no session given; use --session ID or --auto-session in a directory with a recent Claude/Codex session",
              file=sys.stderr)
        return 2
    state = {
        "task": args.task, "attempt": attempt, "producer": args.producer,
        "project": args.project or f"p-{orca_calls._hash(args.cwd)[:12]}",
        "session_hash": orca_calls._hash(session), "start": iso(now), "role": args.role,
        "manifest_revision": 1, "result_revision": 0,
    }
    manifest = manifest_doc(state, now + PROVISIONAL_WINDOW, 1)
    orca_attribution.validate_manifest(manifest)
    code = deliver(args, [manifest], [], now)
    if code == 0 and not args.dry_run:
        args.state_dir.mkdir(parents=True, exist_ok=True)
        state_path(args.state_dir, args.task, attempt).write_text(json.dumps(state), encoding="utf-8")
        print(f"orca_task: began {args.task}/{attempt} (window open for up to 24h; close it with `end`)")
    return code


def cmd_end(args, now: datetime) -> int:
    path = state_path(args.state_dir, args.task, args.attempt)
    if not path.is_file():
        print(f"orca_task: no begin recorded for {args.task}/{args.attempt}", file=sys.stderr)
        return 2
    state = json.loads(path.read_text(encoding="utf-8"))
    state["manifest_revision"] += 1
    state["result_revision"] += 1
    end = max(now, datetime.fromisoformat(state["start"].replace("Z", "+00:00")) + timedelta(seconds=1))
    manifest = manifest_doc(state, end, state["manifest_revision"])
    result = result_doc(state, args, now, state["result_revision"])
    orca_attribution.validate_manifest(manifest)
    orca_attribution.validate_result(result)
    code = deliver(args, [manifest], [result], now)
    if code == 0 and not args.dry_run:
        path.write_text(json.dumps(state), encoding="utf-8")
        print(f"orca_task: ended {args.task}/{args.attempt} as {args.status} (reported, not verified)")
    return code


def build_parser() -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--dry-run", action="store_true", help="print what would be sent")
    common.add_argument("--env-file", type=Path, default=None, help="file with HERMES_TELEMETRY_WEBHOOK_URL/TOKEN")
    common.add_argument("--state-dir", type=Path, default=DEFAULT_STATE_DIR)
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("begin", "end"):
        p = sub.add_parser(name, parents=[common])
        p.add_argument("--task", required=True)
        p.add_argument("--attempt", default="a1")
        p.add_argument("--cwd", default=os.getcwd())
        if name == "begin":
            p.add_argument("--session")
            p.add_argument("--auto-session", action="store_true")
            p.add_argument("--project")
            p.add_argument("--role")
            p.add_argument("--producer", default="orca-task-cli")
        else:
            p.add_argument("--status", required=True, choices=["completed", "failed", "cancelled", "blocked"])
            p.add_argument("--test-id")
            p.add_argument("--exit-code", type=int)
            for key in ("passed", "failed", "skipped"):
                p.add_argument(f"--{key}", type=int)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    now = datetime.now(timezone.utc)
    try:
        return cmd_begin(args, now) if args.command == "begin" else cmd_end(args, now)
    except orca_attribution.InvalidInput as exc:
        print(f"orca_task: invalid input: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
