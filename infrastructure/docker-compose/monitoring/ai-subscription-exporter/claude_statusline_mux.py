#!/usr/bin/env python3
"""Capture Claude rate limits, then preserve an existing status-line command."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

from claude_statusline_capture import format_status, sanitized_status, write_status


def upstream_path() -> Path:
    return Path(
        os.environ.get(
            "CLAUDE_STATUSLINE_UPSTREAM",
            "~/.config/gullinkambi/claude-statusline-upstream.json",
        )
    ).expanduser()


def load_upstream() -> str | None:
    path = upstream_path()
    if not path.exists():
        return None
    with path.open(encoding="utf-8") as handle:
        value: Any = json.load(handle)
    if not isinstance(value, dict) or value.get("type") != "command":
        return None
    command = value.get("command")
    return command if isinstance(command, str) and command else None


def main() -> int:
    raw = sys.stdin.buffer.read()
    try:
        payload = json.loads(raw)
        if not isinstance(payload, dict):
            raise ValueError("status line input must be a JSON object")
        safe = sanitized_status(payload)
        write_status(safe)
    except Exception as exc:
        print(f"Claude usage capture failed: {exc}", file=sys.stderr)
        safe = {"model": "unknown", "rate_limits": {}}

    try:
        command = load_upstream()
    except Exception as exc:
        print(f"Claude upstream status line unavailable: {exc}", file=sys.stderr)
        command = None
    if command:
        completed = subprocess.run(
            ["/bin/sh", "-c", command],
            input=raw,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            check=False,
        )
        sys.stdout.buffer.write(completed.stdout)
        return completed.returncode

    print(format_status(safe))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
