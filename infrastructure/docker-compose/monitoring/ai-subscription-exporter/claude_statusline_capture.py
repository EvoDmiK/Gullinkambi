#!/usr/bin/env python3
"""Capture only Claude subscription rate limits from status-line JSON."""

from __future__ import annotations

import json
import os
import sys
import tempfile
import time
from pathlib import Path
from typing import Any


def sanitized_status(payload: dict[str, Any]) -> dict[str, Any]:
    rate_limits = payload.get("rate_limits")
    if not isinstance(rate_limits, dict):
        rate_limits = {}
    safe_limits: dict[str, dict[str, float]] = {}
    for source in ("five_hour", "seven_day"):
        value = rate_limits.get(source)
        if not isinstance(value, dict):
            continue
        safe: dict[str, float] = {}
        for source_key, target_key in (
            ("used_percentage", "used_percentage"),
            ("resets_at", "resets_at"),
        ):
            number = value.get(source_key)
            if isinstance(number, (int, float)):
                safe[target_key] = float(number)
        if safe:
            safe_limits[source] = safe

    model = payload.get("model")
    model_name = model.get("display_name") if isinstance(model, dict) else None
    return {
        "captured_at": time.time(),
        "rate_limits": safe_limits,
        "model": str(model_name or "unknown"),
    }


def status_path() -> Path:
    return Path(
        os.environ.get(
            "CLAUDE_STATUS_FILE", "~/.cache/gullinkambi/claude-status.json"
        )
    ).expanduser()


def write_status(safe: dict[str, Any]) -> None:
    rate_limits = safe.get("rate_limits")
    if not isinstance(rate_limits, dict) or not rate_limits:
        # Claude does not include rate limits in every status-line payload.
        # Keep the last valid snapshot instead of replacing it with an empty one.
        return
    target = status_path()
    target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", dir=target.parent, delete=False
    ) as handle:
        json.dump(safe, handle, separators=(",", ":"))
        temp_name = handle.name
    os.chmod(temp_name, 0o600)
    os.replace(temp_name, target)


def format_status(safe: dict[str, Any]) -> str:
    limits = safe["rate_limits"]
    five = limits.get("five_hour", {}).get("used_percentage")
    seven = limits.get("seven_day", {}).get("used_percentage")
    parts = [f"Claude {safe['model']}"]
    if five is not None:
        parts.append(f"5h {five:.0f}%")
    if seven is not None:
        parts.append(f"7d {seven:.0f}%")
    return " · ".join(parts)


def main() -> int:
    try:
        payload = json.load(sys.stdin)
        if not isinstance(payload, dict):
            raise ValueError("status line input must be a JSON object")
        safe = sanitized_status(payload)
        write_status(safe)
        print(format_status(safe))
        return 0
    except Exception as exc:
        print(f"Claude usage unavailable: {exc}")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
