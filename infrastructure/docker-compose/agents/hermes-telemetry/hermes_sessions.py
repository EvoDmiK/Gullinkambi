"""Hermes session samples and tool events (metadata only).

Hermes keeps only cumulative per-session counters (no per-call token counts), so usage over time is
rebuilt from periodic samples of those counters: each sample is the cumulative state at one moment and
the database turns consecutive samples into per-interval deltas. Tool events are one row per tool
result message: only the tool name and a coarse outcome leave the machine, never arguments, results
or message text. Session ids are hashed; names, titles, chat/user ids and paths are never read.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

SCHEMA_VERSION = 1
COUNTERS = (
    "message_count", "tool_call_count", "api_call_count", "input_tokens", "output_tokens",
    "cache_read_tokens", "cache_write_tokens", "reasoning_tokens",
)
# outcome: ok | error | nonzero_exit | unknown. Derived locally from the shape of the tool result;
# the result itself is never sent. A non-zero exit code is separate because commands such as
# `grep` use it for "no match", which is not a tool failure.
OUTCOMES = ("ok", "error", "nonzero_exit", "unknown")


def digest(*parts: Any, size: int = 24) -> str:
    return hashlib.sha256("\x1f".join(str(p) for p in parts).encode("utf-8")).hexdigest()[:size]


def _iso(timestamp: float | None) -> str | None:
    if timestamp is None:
        return None
    from datetime import datetime, timezone
    return datetime.fromtimestamp(float(timestamp), tz=timezone.utc).isoformat().replace("+00:00", "Z")


def _int(value: Any) -> int:
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError, OverflowError):
        return 0


def _columns(connection: sqlite3.Connection, table: str) -> set[str]:
    return {row[1] for row in connection.execute(f"PRAGMA table_info({table})")}


FAILED_STATUSES = ("error", "failed", "failure", "timeout", "timed_out", "cancelled", "canceled")
OUTCOME_RULES_VERSION = 2  # bump when tool_outcome changes; the collector then re-sends tool events once


def tool_outcome(content: Any) -> str:
    """Coarse outcome from the shape of a tool result (never its text).

    A JSON object is an error when it carries a truthy `error`, `success: false` or a failed `status`;
    a non-zero `exit_code` is reported separately; any other object is ok. Plain text and JSON arrays
    (MCP and multimodal results) have no reliable signal and stay unknown.
    """
    if not isinstance(content, str):
        return "unknown"
    text = content.lstrip()
    if not text.startswith("{"):
        return "unknown"
    try:
        result = json.loads(text)
    except ValueError:
        return "unknown"  # also covers results truncated by the tool
    if not isinstance(result, dict):
        return "unknown"
    status = result.get("status")
    if (
        result.get("success") is False
        or result.get("error")
        or (isinstance(status, str) and status.lower() in FAILED_STATUSES)
    ):
        return "error"
    exit_code = result.get("exit_code")
    if isinstance(exit_code, int) and not isinstance(exit_code, bool) and exit_code != 0:
        return "nonzero_exit"
    return "ok"


def session_samples(
    connection: sqlite3.Connection, profile: str, cutoff: float, now: float, known_open: Iterable[str] = (),
) -> list[dict[str, Any]]:
    """Current cumulative state of recent (or still tracked) sessions, one dict per session."""
    columns = _columns(connection, "sessions")
    if not {"id", "source", "started_at"}.issubset(columns):
        return []
    wanted = ("id", "source", "chat_type", "model", "parent_session_id", "started_at", "ended_at", "end_reason",
              "expiry_finalized", "archived", "cwd", "billing_provider", "estimated_cost_usd", "actual_cost_usd",
              "cost_status", *COUNTERS)
    select = ", ".join(name if name in columns else f"NULL AS {name}" for name in wanted)
    known = list(known_open)
    where = "started_at >= ? OR COALESCE(ended_at, 0) >= ?"
    params: list[Any] = [cutoff, cutoff]
    if known:
        where += f" OR id IN ({','.join('?' for _ in known)})"
        params += known
    cursor = connection.execute(f"SELECT {select} FROM sessions WHERE {where} ORDER BY started_at", params)
    rows = [dict(zip(wanted, row)) for row in cursor.fetchall()]
    last_activity: dict[str, float] = {}
    if "messages" in {r[0] for r in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}:
        ids = [r["id"] for r in rows]
        for start in range(0, len(ids), 400):
            group = ids[start:start + 400]
            marks = ",".join("?" for _ in group)
            for sid, stamp in connection.execute(
                f"SELECT session_id, MAX(timestamp) FROM messages WHERE session_id IN ({marks}) GROUP BY session_id", group,
            ):
                last_activity[sid] = stamp
    samples = []
    for data in rows:
        session_id = str(data["id"])
        started = float(data["started_at"])
        activity = last_activity.get(session_id)
        sample = {
            "schema_version": SCHEMA_VERSION,
            "profile": profile,
            "session_hash": digest(profile, session_id),
            "parent_hash": digest(profile, data["parent_session_id"]) if data["parent_session_id"] else None,
            "source": str(data["source"] or "unknown")[:64],
            "chat_type": str(data["chat_type"] or "") or None,
            "model": str(data["model"] or "unknown")[:128],
            "provider": str(data["billing_provider"] or "unknown")[:64],
            "project_key": digest(data["cwd"]) if data["cwd"] else None,
            "started_at": _iso(started),
            "last_activity_at": _iso(activity if activity is not None else started),
            "ended_at": _iso(data["ended_at"]),
            "end_reason": str(data["end_reason"] or "")[:128] or None,
            "expiry_finalized": bool(data["expiry_finalized"]),
            "archived": bool(data["archived"]),
            "estimated_cost_usd": float(data["estimated_cost_usd"] or 0.0),
            "actual_cost_usd": float(data["actual_cost_usd"] or 0.0),
            "cost_status": str(data["cost_status"] or "") or None,
            **{name: _int(data[name]) for name in COUNTERS},
        }
        sample["_session_id"] = session_id  # for state bookkeeping only; stripped before sending
        samples.append(sample)
    return samples


def sample_fingerprint(sample: dict[str, Any]) -> str:
    body = {k: v for k, v in sample.items() if not k.startswith("_") and k not in ("sampled_at", "event_key")}
    return hashlib.sha256(json.dumps(body, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def finalize_sample(sample: dict[str, Any], fingerprint: str, sampled_at: float) -> dict[str, Any]:
    out = {k: v for k, v in sample.items() if not k.startswith("_")}
    out["sampled_at"] = _iso(sampled_at)
    out["event_key"] = "hs:" + digest(sample["session_hash"], int(sampled_at), size=32)
    out["fingerprint"] = fingerprint
    return out


def tool_events(
    connection: sqlite3.Connection, profile: str, after_id: int, min_timestamp: float, limit: int, label,
) -> list[dict[str, Any]]:
    """Tool result messages newer than `after_id` (and not older than `min_timestamp` on first run)."""
    columns = _columns(connection, "messages")
    if not {"id", "session_id", "role", "tool_name", "timestamp"}.issubset(columns):
        return []
    content = "content" if "content" in columns else "NULL AS content"
    rows = connection.execute(
        f"SELECT id, session_id, tool_name, timestamp, {content} FROM messages "
        "WHERE role = 'tool' AND tool_name IS NOT NULL AND id > ? AND timestamp >= ? ORDER BY id LIMIT ?",
        (after_id, min_timestamp, limit),
    ).fetchall()
    return [
        {
            "event_key": "ht:" + digest(profile, row[0], size=32),
            "schema_version": SCHEMA_VERSION,
            "profile": profile,
            "session_hash": digest(profile, row[1]),
            "occurred_at": _iso(row[3]),
            "tool": label(row[2]),
            "outcome": tool_outcome(row[4]),
            "_message_id": row[0],
        }
        for row in rows
    ]


# --- tool calls seen in Hermes' own log -------------------------------------------------------
# agent.tool_executor logs every failed tool call (WARNING) and many successful ones (INFO), for every
# tool including MCP, with the session id and duration. Only these fields leave the machine: the error
# text that follows on the same line is never read beyond the match.
TOOL_LOG_RE = re.compile(
    r"^(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d),(\d{3}) \w+ \[([^\]]+)\] agent\.tool_executor: "
    r"(?:tool (\S+) completed \(([\d.]+)s, (\d+) chars\)|Tool (\S+) returned error \(([\d.]+)s\))"
)
LOG_FILES = ("agent.log.2", "agent.log.1", "agent.log")  # oldest first
MAX_LOG_LINE = 4096


def parse_tool_log_line(line: str, profile: str, label) -> dict[str, Any] | None:
    match = TOOL_LOG_RE.match(line[:MAX_LOG_LINE])
    if not match:
        return None
    stamp, millis, session_id, ok_tool, chars, err_tool, err_seconds = (
        match.group(1), match.group(2), match.group(3), match.group(4), match.group(6), match.group(7), match.group(8))
    try:
        occurred = datetime.strptime(f"{stamp}.{millis}", "%Y-%m-%d %H:%M:%S.%f").replace(tzinfo=timezone.utc)  # Hermes logs UTC
    except ValueError:
        return None
    ok = ok_tool is not None
    seconds = float(match.group(5) if ok else err_seconds)
    return {
        "event_key": "hl:" + digest(profile, line.rstrip("\n")[:MAX_LOG_LINE], size=32),
        "schema_version": SCHEMA_VERSION,
        "profile": profile,
        "session_hash": digest(profile, session_id),
        "occurred_at": _iso(occurred.timestamp()),
        "tool": label(ok_tool if ok else err_tool),
        "status": "ok" if ok else "error",
        "duration_ms": round(seconds * 1000),
        "result_chars": int(chars) if ok else None,
        "_ts": occurred.timestamp(),
    }


def read_tool_log_events(
    log_dir: Path, profile: str, offsets: dict[str, Any], min_timestamp: float, limit: int, label,
) -> list[dict[str, Any]]:
    """New tool-call lines since the saved offsets, oldest first, at most `limit`.

    `offsets` maps file name -> [inode, byte offset] and is updated in place only up to the last line
    that was returned, so lines beyond `limit` are picked up on the next run. A rotated file (new inode
    for agent.log) is resumed from where its previous copy stopped.
    """
    events: list[dict[str, Any]] = []
    inodes = {}
    for name in LOG_FILES:
        try:
            inodes[name] = (log_dir / name).stat().st_ino
        except OSError:
            continue
    for name in LOG_FILES:
        if name not in inodes or len(events) >= limit:
            continue
        saved = offsets.get(name)
        start = 0
        if saved and saved[0] == inodes[name]:
            start = saved[1]
        else:
            # Same inode as a file we tracked under another name => it was rotated: continue from there.
            for other, value in offsets.items():
                if other != name and value[0] == inodes[name]:
                    start = value[1]
        path = log_dir / name
        try:
            with path.open("rb") as handle:
                if start > path.stat().st_size:
                    start = 0
                handle.seek(start)
                position = start
                for raw in handle:
                    if not raw.endswith(b"\n"):
                        break  # partial line still being written
                    if len(events) >= limit:
                        break
                    position += len(raw)
                    event = parse_tool_log_line(raw.decode("utf-8", "replace"), profile, label)
                    if event and event["_ts"] >= min_timestamp:
                        events.append(event)
                offsets[name] = [inodes[name], position]
        except OSError:
            continue
    # drop offsets of files that no longer exist under any of the names
    for name in [n for n in offsets if n not in inodes]:
        del offsets[name]
    return events
