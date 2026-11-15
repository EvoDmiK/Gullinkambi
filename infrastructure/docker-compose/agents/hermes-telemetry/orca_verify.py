#!/usr/bin/env python3
"""Run a test command and report the outcome as signed verifier evidence for an ORCA attempt.

    orca_verify.py --task TASK [--attempt a1] --test-id unit -- python3 -m unittest discover -s tests

The command is executed here, so the exit code comes from the tool, not from the agent's claim.
The result is sent with producer_type=verifier and an HMAC signature (key: ORCA_VERIFIER_KEY or
--key-file); the pipeline accepts it as `evidence_verified` only for an approved producer with a
valid signature. The command line and the output are NOT sent: only the exit code, test counts and
the sha256 of the output. The full output is kept in the local state directory (mode 0600) so the
checksum can be re-checked later.

Limits: the key is a shared secret on this host, so this separates "the agent said so" from
"the verifier ran it", not a hostile process that can read the key file.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
import orca_attribution  # noqa: E402
import orca_task  # noqa: E402

DEFAULT_KEY_FILE = Path.home() / ".config" / "orca-task" / "verifier.key"
DEFAULT_PRODUCER = "orca-verifier"
TIMEOUT_EXIT_CODE = 124  # same convention as coreutils `timeout`
MAX_REVISION = 999_999


def parse_counts(output: str) -> dict[str, int]:
    """Best-effort test counts from a unittest or pytest summary; empty when not recognised."""
    counts: dict[str, int] = {}
    ran = re.search(r"^Ran (\d+) tests? in", output, re.M)
    if ran:  # unittest
        total = int(ran.group(1))
        detail = re.search(r"^(?:OK|FAILED)\s*\(([^)]*)\)", output, re.M)
        parts = {k: int(v) for k, v in re.findall(r"(failures|errors|skipped)=(\d+)", detail.group(1))} if detail else {}
        failed, skipped = parts.get("failures", 0) + parts.get("errors", 0), parts.get("skipped", 0)
        return {"passed": max(total - failed - skipped, 0), "failed": failed, "skipped": skipped}
    for number, word in re.findall(r"(\d+) (passed|failed|skipped|errors?)\b", output.splitlines()[-1] if output.strip() else ""):
        counts["failed" if word.startswith("error") else word] = counts.get("failed" if word.startswith("error") else word, 0) + int(number)
    return counts


def run_command(command: list[str], cwd: str, timeout: int | None) -> tuple[int, bytes, float]:
    started = time.monotonic()
    try:
        done = subprocess.run(command, cwd=cwd, capture_output=True, timeout=timeout)
        output, code = done.stdout + done.stderr, done.returncode
    except subprocess.TimeoutExpired as exc:
        output, code = (exc.stdout or b"") + (exc.stderr or b"") + b"\n[orca_verify: timeout]\n", TIMEOUT_EXIT_CODE
    except FileNotFoundError:
        output, code = b"[orca_verify: command not found]\n", 127
    return code, output, time.monotonic() - started


def read_key(args) -> str | None:
    key = os.environ.get("ORCA_VERIFIER_KEY")
    if not key and args.key_file.is_file():
        key = args.key_file.read_text(encoding="utf-8").strip()
    return key or None


def next_revision(state_dir: Path, task: str, attempt: str) -> int:
    path = state_dir / f"{task}__{attempt}.verify.json"
    revision = (json.loads(path.read_text())["revision"] if path.is_file() else 0) + 1
    if revision > MAX_REVISION:
        raise orca_attribution.InvalidInput("result_revision_exhausted")
    return revision


def build_result(args, code: int, output: bytes, revision: int, now: datetime) -> dict[str, Any]:
    handle = f"verify-{args.task}-{args.attempt}-{revision}"
    doc: dict[str, Any] = {
        "schema_version": 1, "event_id": f"ver-{args.task}-{args.attempt}-{revision}",
        "task_id": args.task, "attempt_id": args.attempt, "result_revision": revision,
        "occurred_at": orca_task.iso(now), "producer_id": args.producer, "producer_type": "verifier",
        "evidence_kind": "real", "reported_status": "completed" if code == 0 else "failed",
        "verification_state": "evidence_verified" if code == 0 else "failed",
        "test": {"test_id": args.test_id, "exit_code": code, **parse_counts(output.decode("utf-8", "replace"))},
        "artifact_handle": handle, "artifact_sha256": hashlib.sha256(output).hexdigest(),
    }
    return doc


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--task", required=True)
    parser.add_argument("--attempt", default="a1")
    parser.add_argument("--test-id", required=True)
    parser.add_argument("--producer", default=DEFAULT_PRODUCER)
    parser.add_argument("--cwd", default=os.getcwd())
    parser.add_argument("--timeout", type=int, default=1800)
    parser.add_argument("--key-file", type=Path, default=DEFAULT_KEY_FILE)
    parser.add_argument("--state-dir", type=Path, default=orca_task.DEFAULT_STATE_DIR)
    parser.add_argument("--env-file", type=Path, default=None)
    parser.add_argument("--dry-run", action="store_true", help="run the command and print the signed result without sending it")
    parser.add_argument("command", nargs=argparse.REMAINDER, help="-- <command to run>")
    args = parser.parse_args(argv)
    command = args.command[1:] if args.command[:1] == ["--"] else args.command
    if not command:
        print("orca_verify: give the command after `--`", file=sys.stderr)
        return 2
    key = read_key(args)
    if not key:
        print("orca_verify: no signing key (set ORCA_VERIFIER_KEY or create the key file)", file=sys.stderr)
        return 2

    now = datetime.now(timezone.utc)
    try:
        revision = next_revision(args.state_dir, args.task, args.attempt)
        code, output, seconds = run_command(command, args.cwd, args.timeout)
        doc = build_result(args, code, output, revision, datetime.now(timezone.utc))
        doc["signature"] = orca_attribution.sign_result(doc, key)
        orca_attribution.validate_result(doc)
    except orca_attribution.InvalidInput as exc:
        print(f"orca_verify: invalid input: {exc}", file=sys.stderr)
        return 2

    delivered = orca_task.deliver(args, [], [doc], now)
    if delivered == 0 and not args.dry_run:
        args.state_dir.mkdir(parents=True, exist_ok=True)
        (args.state_dir / f"{args.task}__{args.attempt}.verify.json").write_text(json.dumps({"revision": revision}), encoding="utf-8")
        evidence = args.state_dir / "evidence"
        evidence.mkdir(parents=True, exist_ok=True)
        log = evidence / f"{doc['artifact_handle']}.log"
        log.write_bytes(output)
        os.chmod(log, 0o600)
    print(f"orca_verify: {args.task}/{args.attempt} exit={code} in {seconds:.1f}s -> "
          f"{doc['verification_state']} (evidence {doc['artifact_sha256'][:12]})", file=sys.stderr)
    return delivered if delivered else (0 if code == 0 else 1)


if __name__ == "__main__":
    raise SystemExit(main())
