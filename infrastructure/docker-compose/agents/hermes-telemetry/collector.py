#!/usr/bin/env python3
"""Export privacy-safe Hermes session telemetry to an HTTP webhook."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import socket
import sqlite3
import ssl
import sys
import time
from collections import Counter, defaultdict
from contextlib import closing
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


SCHEMA_VERSION = 3
ORCA_USAGE_REVISION = 3
ORCA_THREAD_USAGE_REVISION = 6
FAILED_END_REASON_MARKERS = ("fail", "error", "crash", "timeout", "killed")
ORCA_USAGE_FILES = {
    "codex": "orca-codex-usage.json",
    "claude": "orca-claude-usage.json",
    "antigravity": "orca-antigravity-usage.json",
}
ORCA_SESSION_ID_RE = re.compile(
    r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
    re.I,
)
SKILL_FILE_RE = re.compile(r"(?:^|[/\\])([A-Za-z0-9][A-Za-z0-9._-]{0,127})[/\\]SKILL\.md\b", re.I)
SKILL_READ_COMMAND_RE = re.compile(r"\b(?:cat|sed|head|tail|less|bat)\b[^\n;]{0,500}", re.I)
USAGE_TIMEZONE = timezone(timedelta(hours=9))


def utc_iso(timestamp: float | int | None = None) -> str | None:
    if timestamp is None:
        return None
    return datetime.fromtimestamp(float(timestamp), tz=timezone.utc).isoformat().replace("+00:00", "Z")


def env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Settings:
    hermes_home: Path
    state_path: Path
    webhook_url: str
    webhook_token: str
    interval_seconds: int
    request_timeout_seconds: int
    batch_size: int
    initial_backfill_hours: int
    rescan_hours: int
    verify_tls: bool
    collector_id: str
    include_kanban_titles: bool
    orca_usage_dir: Path | None = None
    orca_codex_sessions_dir: Path | None = None
    orca_codex_state_db: Path | None = None
    orca_claude_projects_dir: Path | None = None
    orca_worktree_roots: tuple[Path, ...] = ()
    collect_orca_usage: bool = False
    collect_orca_calls: bool = False
    orca_origin_namespace: str = "orca-local"
    orca_call_lookback_hours: int = 48
    orca_call_batch_limit: int = 500
    collect_hermes_sessions: bool = True
    hermes_session_lookback_hours: int = 168
    hermes_tool_batch_limit: int = 500
    excluded_profiles: frozenset[str] = frozenset()

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(
            hermes_home=Path(os.getenv("HERMES_TELEMETRY_HOME", "/opt/data")),
            state_path=Path(
                os.getenv(
                    "HERMES_TELEMETRY_STATE_PATH",
                    "/var/lib/hermes-telemetry/cursor.json",
                )
            ),
            webhook_url=os.getenv("HERMES_TELEMETRY_WEBHOOK_URL", "").strip(),
            webhook_token=os.getenv("HERMES_TELEMETRY_WEBHOOK_TOKEN", "").strip(),
            interval_seconds=max(0, int(os.getenv("HERMES_TELEMETRY_INTERVAL_SECONDS", "60"))),
            request_timeout_seconds=max(
                1, int(os.getenv("HERMES_TELEMETRY_REQUEST_TIMEOUT_SECONDS", "60"))
            ),
            batch_size=max(1, int(os.getenv("HERMES_TELEMETRY_BATCH_SIZE", "100"))),
            initial_backfill_hours=max(
                1, int(os.getenv("HERMES_TELEMETRY_INITIAL_BACKFILL_HOURS", "24"))
            ),
            rescan_hours=max(1, int(os.getenv("HERMES_TELEMETRY_RESCAN_HOURS", "168"))),
            verify_tls=env_bool("HERMES_TELEMETRY_VERIFY_TLS", True),
            collector_id=os.getenv("HERMES_TELEMETRY_COLLECTOR_ID", socket.gethostname()).strip()
            or socket.gethostname(),
            include_kanban_titles=env_bool("HERMES_TELEMETRY_INCLUDE_KANBAN_TITLES", False),
            orca_usage_dir=Path(
                os.getenv("HERMES_TELEMETRY_ORCA_USAGE_DIR", "/opt/orca-state")
            ),
            orca_codex_sessions_dir=Path(
                os.getenv(
                    "HERMES_TELEMETRY_ORCA_CODEX_SESSIONS_DIR",
                    "/opt/orca-raw/codex",
                )
            ),
            orca_codex_state_db=Path(
                os.getenv(
                    "HERMES_TELEMETRY_ORCA_CODEX_STATE_DB",
                    "/opt/orca-raw/codex-state/state_5.sqlite",
                )
            ),
            orca_claude_projects_dir=Path(
                os.getenv(
                    "HERMES_TELEMETRY_ORCA_CLAUDE_PROJECTS_DIR",
                    "/opt/orca-raw/claude",
                )
            ),
            orca_worktree_roots=tuple(
                Path(item.strip())
                for item in os.getenv(
                    "HERMES_TELEMETRY_ORCA_WORKTREE_ROOTS",
                    "/Users/example/orca,/Volumes/Example/orca",
                ).split(",")
                if item.strip()
            ),
            collect_orca_usage=env_bool("HERMES_TELEMETRY_COLLECT_ORCA_USAGE", True),
            collect_orca_calls=env_bool("HERMES_TELEMETRY_COLLECT_ORCA_CALLS", False),
            orca_origin_namespace=os.getenv("HERMES_TELEMETRY_ORCA_ORIGIN_NAMESPACE", "orca-local").strip() or "orca-local",
            orca_call_lookback_hours=max(1, int(os.getenv("HERMES_TELEMETRY_ORCA_CALL_LOOKBACK_HOURS", "48"))),
            orca_call_batch_limit=max(1, int(os.getenv("HERMES_TELEMETRY_ORCA_CALL_BATCH_LIMIT", "500"))),
            collect_hermes_sessions=env_bool("HERMES_TELEMETRY_COLLECT_HERMES_SESSIONS", True),
            hermes_session_lookback_hours=max(1, int(os.getenv("HERMES_TELEMETRY_SESSION_LOOKBACK_HOURS", "168"))),
            hermes_tool_batch_limit=max(1, int(os.getenv("HERMES_TELEMETRY_TOOL_BATCH_LIMIT", "500"))),
            excluded_profiles=frozenset(
                item.strip()
                for item in os.getenv("HERMES_TELEMETRY_EXCLUDED_PROFILES", "").split(",")
                if item.strip()
            ),
        )


def discover_databases(
    hermes_home: Path,
    excluded_profiles: frozenset[str] = frozenset(),
) -> list[tuple[str, Path]]:
    databases: list[tuple[str, Path]] = []
    default_db = hermes_home / "state.db"
    if "default" not in excluded_profiles and default_db.is_file():
        databases.append(("default", default_db))

    profiles_dir = hermes_home / "profiles"
    if profiles_dir.is_dir():
        for profile_dir in sorted(profiles_dir.iterdir(), key=lambda item: item.name):
            db_path = profile_dir / "state.db"
            if (
                profile_dir.name not in excluded_profiles
                and profile_dir.is_dir()
                and db_path.is_file()
            ):
                databases.append((profile_dir.name, db_path))
    return databases


def discover_kanban_databases(hermes_home: Path) -> list[tuple[str, Path]]:
    databases: list[tuple[str, Path]] = []
    default_db = hermes_home / "kanban.db"
    if default_db.is_file():
        databases.append(("default", default_db))

    boards_dir = hermes_home / "kanban" / "boards"
    if boards_dir.is_dir():
        for board_dir in sorted(boards_dir.iterdir(), key=lambda item: item.name):
            db_path = board_dir / "kanban.db"
            if board_dir.is_dir() and db_path.is_file():
                databases.append((board_dir.name, db_path))
    return databases


def load_state(path: Path) -> dict[str, Any]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(raw, dict) and raw.get("schema_version") == SCHEMA_VERSION:
            raw.setdefault("runs", {})
            raw.setdefault("goals", {})
            return raw
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        pass
    return {"schema_version": SCHEMA_VERSION, "runs": {}, "goals": {}}


def save_state(path: Path, state: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(state, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def sqlite_readonly(path: Path) -> sqlite3.Connection:
    try:
        connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=5)
        connection.execute("SELECT 1").fetchone()
    except sqlite3.OperationalError:
        # Some inactive profile databases reject file locking despite being readable.
        try:
            connection.close()
        except UnboundLocalError:
            pass
        connection = sqlite3.connect(
            f"file:{path}?mode=ro&immutable=1", uri=True, timeout=5
        )
    connection.row_factory = sqlite3.Row
    return connection


def table_columns(connection: sqlite3.Connection, table: str) -> set[str]:
    return {str(row[1]) for row in connection.execute(f"PRAGMA table_info({table})")}


def parse_model_config(raw: Any) -> tuple[str, str]:
    if not raw:
        return "", ""
    try:
        value = json.loads(str(raw))
    except (json.JSONDecodeError, TypeError):
        return "", ""
    if not isinstance(value, dict):
        return "", ""
    return str(value.get("model") or value.get("default") or ""), str(
        value.get("provider") or ""
    )


def session_status(ended_at: Any, end_reason: Any) -> str:
    if ended_at is None:
        return "open"
    reason = str(end_reason or "").lower()
    if any(marker in reason for marker in FAILED_END_REASON_MARKERS):
        return "failed"
    return "completed"


def chunks(values: list[str], size: int = 400) -> Iterable[list[str]]:
    for start in range(0, len(values), size):
        yield values[start : start + size]


def collect_tool_usage(connection: sqlite3.Connection, session_ids: list[str]) -> dict[str, dict[str, int]]:
    explicit: dict[str, Counter[str]] = defaultdict(Counter)
    fallback: dict[str, Counter[str]] = defaultdict(Counter)
    if not session_ids or "messages" not in {
        str(row[0])
        for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }:
        return {}

    columns = table_columns(connection, "messages")
    if "session_id" not in columns:
        return {}

    for group in chunks(session_ids):
        placeholders = ",".join("?" for _ in group)
        if "tool_calls" in columns:
            # Extract only function names inside SQLite. Tool arguments never enter
            # the collector process or its outbound payload.
            query = f"""
                SELECT m.session_id,
                       COALESCE(
                           json_extract(call.value, '$.function.name'),
                           json_extract(call.value, '$.name')
                       ) AS name
                FROM messages AS m
                JOIN json_each(
                    CASE WHEN json_valid(m.tool_calls) THEN m.tool_calls ELSE '[]' END
                ) AS call
                WHERE m.session_id IN ({placeholders})
            """
            try:
                for row in connection.execute(query, group):
                    name = row["name"]
                    if isinstance(name, str) and name.strip():
                        explicit[str(row["session_id"])][name.strip()[:128]] += 1
            except sqlite3.OperationalError:
                # Older SQLite builds may not provide JSON functions. It is safer
                # to omit structured usage than to pull raw tool arguments.
                pass

        if "tool_name" in columns:
            query = (
                f"SELECT session_id, tool_name FROM messages "
                f"WHERE session_id IN ({placeholders}) AND tool_name IS NOT NULL"
            )
            for row in connection.execute(query, group):
                tool_name = row["tool_name"]
                if isinstance(tool_name, str) and tool_name.strip():
                    fallback[str(row["session_id"])][tool_name.strip()[:128]] += 1

    result: dict[str, dict[str, int]] = {}
    for session_id in session_ids:
        selected = explicit.get(session_id) or fallback.get(session_id)
        if selected:
            result[session_id] = dict(sorted(selected.items()))
    return result


def select_sessions(
    connection: sqlite3.Connection,
    cutoff: float,
    tracked_open_ids: list[str],
) -> list[sqlite3.Row]:
    columns = table_columns(connection, "sessions")
    required = {"id", "source", "started_at"}
    if not required.issubset(columns):
        return []

    wanted = [
        "id",
        "source",
        "model",
        "model_config",
        "parent_session_id",
        "started_at",
        "ended_at",
        "end_reason",
        "message_count",
        "tool_call_count",
        "input_tokens",
        "output_tokens",
        "cache_read_tokens",
        "cache_write_tokens",
        "reasoning_tokens",
        "billing_provider",
        "billing_mode",
        "estimated_cost_usd",
        "actual_cost_usd",
        "cost_status",
        "pricing_version",
        "api_call_count",
        "rewind_count",
    ]
    expressions = [name if name in columns else f"NULL AS {name}" for name in wanted]
    where = "started_at >= ? OR COALESCE(ended_at, 0) >= ?"
    params: list[Any] = [cutoff, cutoff]
    if tracked_open_ids:
        where += f" OR id IN ({','.join('?' for _ in tracked_open_ids)})"
        params.extend(tracked_open_ids)
    return list(
        connection.execute(
            f"SELECT {', '.join(expressions)} FROM sessions WHERE {where} ORDER BY started_at",
            params,
        )
    )


def normalize_session(profile: str, row: sqlite3.Row, tool_usage: dict[str, int]) -> dict[str, Any]:
    fallback_model, fallback_provider = parse_model_config(row["model_config"])
    started_at = float(row["started_at"])
    ended_raw = row["ended_at"]
    ended_at = float(ended_raw) if ended_raw is not None else None
    session_id = str(row["id"])
    status = session_status(ended_at, row["end_reason"])
    return {
        "event_id": f"hermes:{profile}:{session_id}",
        "session_id": session_id,
        "profile": profile,
        "source": str(row["source"] or "unknown"),
        "status": status,
        "end_reason": str(row["end_reason"] or "") or None,
        "started_at": utc_iso(started_at),
        "ended_at": utc_iso(ended_at),
        "duration_ms": max(0, round((ended_at - started_at) * 1000)) if ended_at else None,
        "model": str(row["model"] or fallback_model or "unknown"),
        "provider": str(row["billing_provider"] or fallback_provider or "unknown"),
        "billing_mode": str(row["billing_mode"] or "") or None,
        "message_count": int(row["message_count"] or 0),
        "tool_call_count": int(row["tool_call_count"] or 0),
        "api_call_count": int(row["api_call_count"] or 0),
        "input_tokens": int(row["input_tokens"] or 0),
        "output_tokens": int(row["output_tokens"] or 0),
        "cache_read_tokens": int(row["cache_read_tokens"] or 0),
        "cache_write_tokens": int(row["cache_write_tokens"] or 0),
        "reasoning_tokens": int(row["reasoning_tokens"] or 0),
        "estimated_cost_usd": float(row["estimated_cost_usd"] or 0.0),
        "actual_cost_usd": float(row["actual_cost_usd"] or 0.0),
        "cost_status": str(row["cost_status"] or "") or None,
        "pricing_version": str(row["pricing_version"] or "") or None,
        "rewind_count": int(row["rewind_count"] or 0),
        "parent_session_id": str(row["parent_session_id"] or "") or None,
        "tool_usage": tool_usage,
    }


def select_goal_states(connection: sqlite3.Connection) -> list[sqlite3.Row]:
    tables = {
        str(row[0])
        for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }
    if "state_meta" not in tables:
        return []
    columns = table_columns(connection, "state_meta")
    if not {"key", "value"}.issubset(columns):
        return []
    return list(
        connection.execute(
            "SELECT key, value FROM state_meta WHERE key LIKE 'goal:%' ORDER BY key"
        )
    )


def normalize_goal_run(
    profile: str,
    row: sqlite3.Row,
    session_models: dict[str, str],
) -> dict[str, Any] | None:
    key = str(row["key"] or "")
    session_id = key.removeprefix("goal:") if key.startswith("goal:") else ""
    try:
        state = json.loads(str(row["value"] or ""))
    except (TypeError, json.JSONDecodeError):
        return None
    if not session_id or not isinstance(state, dict):
        return None

    try:
        started_at = float(state.get("created_at") or 0)
        last_turn_at = float(state.get("last_turn_at") or 0)
    except (TypeError, ValueError, OverflowError):
        return None
    if started_at <= 0:
        return None

    status = str(state.get("status") or "unknown")
    ended_at = last_turn_at if status in {"done", "paused", "cleared"} and last_turn_at > 0 else None
    created_micros = round(started_at * 1_000_000)
    return {
        "event_id": f"hermes-goal:{profile}:{created_micros}",
        "session_id": session_id,
        "profile": profile,
        "model": session_models.get(session_id, "unknown"),
        "status": status,
        "turns_used": int(state.get("turns_used") or 0),
        "max_turns": int(state.get("max_turns") or 0),
        "started_at": utc_iso(started_at),
        "ended_at": utc_iso(ended_at),
        "duration_ms": max(0, round((ended_at - started_at) * 1000)) if ended_at else None,
        "last_verdict": str(state.get("last_verdict") or "") or None,
        "paused_reason": str(state.get("paused_reason") or "")[:240] or None,
    }


def fingerprint(run: dict[str, Any]) -> str:
    encoded = json.dumps(run, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def nonnegative_int(value: Any) -> int:
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError, OverflowError):
        return 0


def privacy_safe_project_label(value: Any, project_key: Any = None) -> str:
    key = str(project_key or "").replace("\\", "/")
    path = key.rsplit("::", 1)[-1].removeprefix("cwd:")
    if path.startswith("/"):
        parts = Path(path).parts
        for index, part in enumerate(parts):
            if part == "orca":
                rest = parts[index + 1:]
                project_index = rest.index("workspaces") + 1 if "workspaces" in rest else 0
                if project_index < len(rest):
                    return rest[project_index][:128]
    label = str(value or "unknown").strip().replace("\\", "/").rstrip("/")
    return (label.rsplit("/", 1)[-1] or "unknown")[:128]


def hashed_identifier(value: Any) -> str:
    return hashlib.sha256(str(value or "unknown").encode("utf-8")).hexdigest()[:24]


def normalize_orca_usage(agent: str, item: dict[str, Any]) -> dict[str, Any] | None:
    day = str(item.get("day") or "")
    try:
        datetime.strptime(day, "%Y-%m-%d")
    except ValueError:
        return None

    model = str(item.get("model") or "unknown")[:128]
    project_key = item.get("projectKey") or item.get("projectLabel") or "unknown"
    input_tokens = nonnegative_int(item.get("inputTokens"))
    output_tokens = nonnegative_int(item.get("outputTokens"))

    if agent == "codex":
        cache_read_tokens = nonnegative_int(item.get("cachedInputTokens"))
        cache_write_tokens = 0
        reasoning_tokens = nonnegative_int(item.get("reasoningOutputTokens"))
        request_count = nonnegative_int(item.get("eventCount"))
        total_tokens = nonnegative_int(item.get("totalTokens")) or (
            input_tokens + output_tokens
        )
    elif agent == "claude":
        cache_read_tokens = nonnegative_int(item.get("cacheReadTokens"))
        cache_write_tokens = nonnegative_int(item.get("cacheWriteTokens"))
        reasoning_tokens = 0
        request_count = nonnegative_int(item.get("turnCount"))
        total_tokens = (
            input_tokens + cache_read_tokens + cache_write_tokens + output_tokens
        )
    elif agent == "antigravity":
        cache_read_tokens = nonnegative_int(item.get("cacheReadTokens"))
        cache_write_tokens = nonnegative_int(item.get("cacheWriteTokens"))
        reasoning_tokens = nonnegative_int(item.get("reasoningTokens"))
        request_count = nonnegative_int(item.get("requestCount"))
        total_tokens = nonnegative_int(item.get("totalTokens")) or (
            input_tokens + output_tokens
        )
    else:
        return None

    project_hash = hashed_identifier(project_key)
    event_id = f"orca-usage:{agent}:{day}:{hashed_identifier(f'{model}:{project_key}')}"
    return {
        "event_id": event_id,
        "usage_date": day,
        "agent": agent,
        "model": model,
        "project_label": privacy_safe_project_label(item.get("projectLabel"), project_key),
        "project_hash": project_hash,
        "request_count": request_count,
        "input_tokens": input_tokens,
        "cache_read_tokens": cache_read_tokens,
        "cache_write_tokens": cache_write_tokens,
        "output_tokens": output_tokens,
        "reasoning_tokens": reasoning_tokens,
        "total_tokens": total_tokens,
    }


ORCA_USAGE_NUMERIC_FIELDS = (
    "request_count",
    "input_tokens",
    "cache_read_tokens",
    "cache_write_tokens",
    "output_tokens",
    "reasoning_tokens",
    "total_tokens",
)


def merge_orca_usage(rows: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    merged: dict[str, dict[str, Any]] = {}
    for row in rows:
        event_id = str(row["event_id"])
        if event_id not in merged:
            merged[event_id] = dict(row)
            continue
        target = merged[event_id]
        for field in ORCA_USAGE_NUMERIC_FIELDS:
            target[field] = nonnegative_int(target.get(field)) + nonnegative_int(
                row.get(field)
            )
        if "skill_names" in target or "skill_names" in row:
            target["skill_names"] = sorted(set(target.get("skill_names", [])) | set(row.get("skill_names", [])))
    return list(merged.values())


def is_orca_worktree(cwd: Any, roots: tuple[Path, ...]) -> bool:
    if not cwd:
        return False
    path = Path(str(cwd))
    if not path.is_absolute():
        return False
    for root in roots:
        try:
            path.relative_to(root)
            return True
        except ValueError:
            continue
    return False


def usage_day(timestamp: Any) -> str | None:
    value = str(timestamp or "")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return None
    return parsed.astimezone(USAGE_TIMEZONE).date().isoformat()


def raw_orca_row(
    agent: str,
    timestamp: Any,
    model: Any,
    cwd: Any,
    usage: dict[str, Any],
    thread_id: str | None = None,
) -> dict[str, Any] | None:
    day = usage_day(timestamp)
    if day is None:
        return None
    if agent == "codex":
        item = {
            "day": day,
            "model": model,
            "projectKey": cwd,
            "projectLabel": cwd,
            "eventCount": 1,
            "inputTokens": usage.get("input_tokens"),
            "cachedInputTokens": usage.get("cached_input_tokens"),
            "outputTokens": usage.get("output_tokens"),
            "reasoningOutputTokens": usage.get("reasoning_output_tokens"),
            "totalTokens": usage.get("total_tokens"),
        }
    elif agent == "claude":
        item = {
            "day": day,
            "model": model,
            "projectKey": cwd,
            "projectLabel": cwd,
            "turnCount": 1,
            "inputTokens": usage.get("input_tokens"),
            "cacheReadTokens": usage.get("cache_read_input_tokens"),
            "cacheWriteTokens": usage.get("cache_creation_input_tokens"),
            "outputTokens": usage.get("output_tokens"),
        }
    elif agent == "antigravity":
        item = {
            "day": day,
            "model": model,
            "projectKey": cwd,
            "projectLabel": cwd,
            "requestCount": 1,
            "inputTokens": usage.get("input_tokens"),
            "cacheReadTokens": usage.get("cache_read_tokens"),
            "cacheWriteTokens": usage.get("cache_write_tokens"),
            "outputTokens": usage.get("output_tokens"),
            "reasoningTokens": usage.get("reasoning_tokens"),
            "totalTokens": usage.get("total_tokens"),
        }
    else:
        return None
    row = normalize_orca_usage(agent, item)
    if row is not None and thread_id is not None:
        row["thread_id"] = thread_id
        group_hash = hashed_identifier(f"{row['model']}:{row['project_hash']}")
        row["event_id"] = f"orca-thread:{agent}:{thread_id}:{day}:{group_hash}"
    return row


def normalize_thread_name(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    name = " ".join(value.split())[:160]
    return name or None


def skill_names_from_tool_call(tool_name: Any, arguments: Any) -> set[str]:
    if isinstance(arguments, str):
        try:
            arguments = json.loads(arguments)
        except json.JSONDecodeError:
            return set()
    if not isinstance(arguments, dict):
        return set()

    if tool_name == "Skill":
        name = arguments.get("skill") or arguments.get("name")
        return {name[:128]} if isinstance(name, str) and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}", name) else set()

    if tool_name == "Read":
        source = str(arguments.get("file_path") or "")
    elif tool_name in {"Bash", "exec_command", "exec"}:
        source = str(arguments.get("command") or arguments.get("cmd") or arguments.get("code") or "")
        return {
            name
            for command in SKILL_READ_COMMAND_RE.findall(source)
            for name in SKILL_FILE_RE.findall(command)
        }
    else:
        return set()
    return set(SKILL_FILE_RE.findall(source))


def codex_thread_names(path: Path | None) -> dict[str, str]:
    if path is None or not path.is_file():
        return {}
    try:
        with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as connection:
            rows = connection.execute(
                "SELECT id, COALESCE(NULLIF(name, ''), title) FROM threads"
            )
            return {
                str(thread_id): name
                for thread_id, raw_name in rows
                if (name := normalize_thread_name(raw_name)) is not None
            }
    except sqlite3.Error as exc:
        print(f"telemetry: cannot read Codex thread names: {exc}", file=sys.stderr)
        return {}


def parse_codex_usage_file(
    path: Path,
    roots: tuple[Path, ...],
    thread_id: str | None = None,
) -> list[dict[str, Any]]:
    current_cwd: str | None = None
    current_model: str = "unknown"
    responses: dict[str, dict[str, Any]] = {}
    skill_names: set[str] = set()
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(event, dict):
                continue
            payload = event.get("payload")
            if not isinstance(payload, dict):
                continue
            if event.get("type") == "response_item" and payload.get("type") in ("function_call", "custom_tool_call"):
                skill_names.update(skill_names_from_tool_call(payload.get("name"), payload.get("arguments")))
            if event.get("type") == "session_meta":
                current_cwd = str(payload.get("cwd") or "") or None
            elif event.get("type") == "turn_context":
                current_cwd = str(payload.get("cwd") or current_cwd or "") or None
                current_model = str(payload.get("model") or current_model or "unknown")
            elif event.get("type") == "token_usage_record" and is_orca_worktree(
                current_cwd, roots
            ):
                usage = payload.get("usage")
                if not isinstance(usage, dict):
                    continue
                response_id = str(payload.get("response_id") or "")
                row = raw_orca_row(
                    "codex", event.get("timestamp"), current_model, current_cwd, usage,
                    thread_id,
                )
                if response_id and row is not None:
                    responses[response_id] = row
    rows = merge_orca_usage(responses.values())
    if thread_id is not None:
        for row in rows:
            row["skill_names"] = sorted(skill_names)
    return rows


def parse_claude_usage_file(
    path: Path,
    roots: tuple[Path, ...],
    thread_id: str | None = None,
) -> list[dict[str, Any]]:
    messages: dict[str, dict[str, Any]] = {}
    thread_name: str | None = None
    skill_names: set[str] = set()
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(event, dict):
                continue
            if event.get("type") == "ai-title":
                thread_name = normalize_thread_name(event.get("aiTitle")) or thread_name
                continue
            if event.get("type") != "assistant":
                continue
            message = event.get("message")
            if not isinstance(message, dict):
                continue
            content = message.get("content")
            if isinstance(content, list):
                for block in content:
                    if isinstance(block, dict) and block.get("type") == "tool_use":
                        skill_names.update(skill_names_from_tool_call(block.get("name"), block.get("input")))
            usage = message.get("usage")
            cwd = event.get("cwd")
            if not isinstance(usage, dict) or not is_orca_worktree(cwd, roots):
                continue
            message_id = str(message.get("id") or "")
            row = raw_orca_row(
                "claude", event.get("timestamp"), message.get("model"), cwd,
                usage, thread_id,
            )
            if message_id and row is not None:
                messages[message_id] = row
    rows = merge_orca_usage(messages.values())
    if thread_id is not None:
        for row in rows:
            row["skill_names"] = sorted(skill_names)
            if thread_name is not None:
                row["thread_name"] = thread_name
    return rows


def collect_raw_orca_usage(
    settings: Settings,
    state: dict[str, Any],
    now: float,
    per_thread: bool = False,
    include_old: bool = False,
) -> list[dict[str, Any]]:
    cutoff = now - settings.rescan_hours * 3600
    sources = (
        ("codex", settings.orca_codex_sessions_dir, parse_codex_usage_file),
        ("claude", settings.orca_claude_projects_dir, parse_claude_usage_file),
    )
    cached_sources = state.setdefault(
        "orca_thread_raw_files" if per_thread else "orca_raw_files", {}
    )
    rows: list[dict[str, Any]] = []

    for agent, root, parser in sources:
        if root is None or not root.is_dir():
            continue
        source_cache = cached_sources.setdefault(agent, {})
        active_keys: set[str] = set()
        for path in root.rglob("*.jsonl"):
            try:
                stat = path.stat()
            except OSError:
                continue
            if not per_thread and not include_old and stat.st_mtime < cutoff:
                continue
            thread_match = ORCA_SESSION_ID_RE.search(path.stem) if per_thread else None
            if per_thread and thread_match is None:
                continue
            key = hashed_identifier(path.relative_to(root).as_posix())
            active_keys.add(key)
            saved = source_cache.get(key, {})
            if (
                saved.get("mtime_ns") == stat.st_mtime_ns
                and saved.get("size") == stat.st_size
            ):
                file_rows = saved.get("rows", [])
            else:
                try:
                    file_rows = parser(
                        path, settings.orca_worktree_roots,
                        thread_match.group(0) if thread_match else None,
                    )
                except OSError as exc:
                    print(
                        f"telemetry: cannot read Orca {agent} session: {exc}",
                        file=sys.stderr,
                    )
                    continue
                source_cache[key] = {
                    "mtime_ns": stat.st_mtime_ns,
                    "size": stat.st_size,
                    "rows": file_rows,
                }
            if isinstance(file_rows, list):
                rows.extend(item for item in file_rows if isinstance(item, dict))
        for key in set(source_cache) - active_keys:
            del source_cache[key]

    return merge_orca_usage(rows)


def antigravity_thread_rows(settings: Settings) -> list[dict[str, Any]]:
    """Per-conversation rows from the LaunchAgent document, keyed by hashed IDs."""
    if settings.orca_usage_dir is None:
        return []
    path = settings.orca_usage_dir / ORCA_USAGE_FILES["antigravity"]
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return []
    except (json.JSONDecodeError, OSError) as exc:
        print(f"telemetry: cannot read Orca antigravity usage: {exc}", file=sys.stderr)
        return []
    threads = document.get("threadAggregates", []) if isinstance(document, dict) else []
    if not isinstance(threads, list):
        return []

    rows: list[dict[str, Any]] = []
    for item in threads:
        if not isinstance(item, dict):
            continue
        thread_id = str(item.get("threadId") or "")
        if re.fullmatch(r"[0-9a-f]{24}", thread_id) is None:
            continue
        row = normalize_orca_usage("antigravity", item)
        if row is None:
            continue
        group_hash = hashed_identifier(f"{row['model']}:{row['project_hash']}")
        row["thread_id"] = thread_id
        row["event_id"] = (
            f"orca-thread:antigravity:{thread_id}:{row['usage_date']}:{group_hash}"
        )
        if name := normalize_thread_name(item.get("threadName")):
            row["thread_name"] = name
        rows.append(row)
    return rows


def collect_orca_thread_usage_changes(
    settings: Settings,
    state: dict[str, Any],
    now: float | None = None,
) -> list[dict[str, Any]]:
    if not settings.collect_orca_usage:
        return []
    if state.get("orca_thread_usage_revision") != ORCA_THREAD_USAGE_REVISION:
        # Replay records that an immediate webhook response may have acknowledged
        # before the database write failed. The ingest function is idempotent.
        state["orca_thread_usage"] = {}
        state["orca_thread_raw_files"] = {}
        state["orca_thread_usage_revision"] = ORCA_THREAD_USAGE_REVISION
    rows = collect_raw_orca_usage(
        settings, state, time.time() if now is None else now, per_thread=True
    )
    names = codex_thread_names(settings.orca_codex_state_db)
    for row in rows:
        if row["agent"] == "codex" and (name := names.get(row["thread_id"])):
            row["thread_name"] = name
    rows.extend(antigravity_thread_rows(settings))
    usage_state = state.setdefault("orca_thread_usage", {})
    changes: list[dict[str, Any]] = []
    for item in rows:
        digest = fingerprint(item)
        event_id = item["event_id"]
        if usage_state.get(event_id, {}).get("fingerprint") != digest:
            changes.append({**item, "fingerprint": digest})
        usage_state[event_id] = {"fingerprint": digest}
    return changes


def collect_orca_usage_changes(
    settings: Settings,
    state: dict[str, Any],
    now: float | None = None,
) -> list[dict[str, Any]]:
    if not settings.collect_orca_usage or settings.orca_usage_dir is None:
        return []

    full_rescan = state.get("orca_usage_revision") != ORCA_USAGE_REVISION
    if full_rescan:
        state["orca_usage"] = {}
        state["orca_raw_files"] = {}
        state["orca_usage_revision"] = ORCA_USAGE_REVISION

    now = time.time() if now is None else now
    raw_start_day = datetime.fromtimestamp(
        now - settings.rescan_hours * 3600, tz=USAGE_TIMEZONE
    ).strftime("%Y-%m-%d")
    all_rows: list[dict[str, Any]] = []
    usage_state = state.setdefault("orca_usage", {})
    for agent, filename in ORCA_USAGE_FILES.items():
        path = settings.orca_usage_dir / filename
        try:
            document = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            continue
        except (json.JSONDecodeError, OSError) as exc:
            print(f"telemetry: cannot read Orca {agent} usage: {exc}", file=sys.stderr)
            continue

        daily = document.get("dailyAggregates", []) if isinstance(document, dict) else []
        if not isinstance(daily, list):
            print(f"telemetry: Orca {agent} dailyAggregates is not an array", file=sys.stderr)
            continue

        for raw_item in daily:
            if not isinstance(raw_item, dict):
                continue
            if raw_item.get("worktreeId") is None and raw_item.get("repoId") is None:
                continue
            item = normalize_orca_usage(agent, raw_item)
            if item is None or (
                agent != "antigravity" and item["usage_date"] >= raw_start_day
            ):
                continue
            all_rows.append(item)

    raw_rows = collect_raw_orca_usage(settings, state, now, include_old=full_rescan)
    if full_rescan:
        # Historical Orca snapshots and session logs can cover the same day.
        # Prefer raw rows when both sources describe one repository and model.
        raw_groups = {
            (row["agent"], row["usage_date"], row["model"], row["project_label"])
            for row in raw_rows
        }
        all_rows = [
            row for row in all_rows
            if (row["agent"], row["usage_date"], row["model"], row["project_label"])
            not in raw_groups
        ]
    all_rows.extend(raw_rows)
    changes: list[dict[str, Any]] = []
    for item in merge_orca_usage(all_rows):
        digest = fingerprint(item)
        event_id = item["event_id"]
        if usage_state.get(event_id, {}).get("fingerprint") != digest:
            changes.append({**item, "fingerprint": digest})
        usage_state[event_id] = {"fingerprint": digest}

    return changes


def int_to_iso(value: Any) -> str | None:
    if value in (None, ""):
        return None
    try:
        return utc_iso(float(value))
    except (TypeError, ValueError, OverflowError):
        return None


def json_keys(value: Any) -> list[str]:
    if not value:
        return []
    try:
        parsed = json.loads(str(value))
    except (TypeError, json.JSONDecodeError):
        return []
    if isinstance(parsed, dict):
        return sorted(str(key)[:128] for key in parsed.keys())
    return []


def json_size(value: Any) -> int:
    if not value:
        return 0
    try:
        parsed = json.loads(str(value))
    except (TypeError, json.JSONDecodeError):
        return 0
    if isinstance(parsed, (dict, list)):
        return len(parsed)
    return 0


def bool_int(value: Any) -> int:
    return 1 if value not in (None, "", 0, "0") else 0


def select_all(connection: sqlite3.Connection, table: str) -> list[sqlite3.Row]:
    tables = {str(row[0]) for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if table not in tables:
        return []
    return list(connection.execute(f"SELECT * FROM {table}"))


def count_by_task(connection: sqlite3.Connection, table: str) -> dict[str, int]:
    tables = {str(row[0]) for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if table not in tables:
        return {}
    columns = table_columns(connection, table)
    if "task_id" not in columns:
        return {}
    return {
        str(row["task_id"]): int(row["count"])
        for row in connection.execute(f"SELECT task_id, count(*) AS count FROM {table} GROUP BY task_id")
    }


def latest_runs_by_task(connection: sqlite3.Connection) -> dict[str, sqlite3.Row]:
    rows = select_all(connection, "task_runs")
    latest: dict[str, sqlite3.Row] = {}
    for row in rows:
        task_id = str(row["task_id"])
        current = latest.get(task_id)
        if current is None or int(row["id"] or 0) > int(current["id"] or 0):
            latest[task_id] = row
    return latest


def normalize_kanban_task(
    board: str,
    row: sqlite3.Row,
    include_title: bool,
    dependency_counts: dict[str, int],
    child_counts: dict[str, int],
    comment_counts: dict[str, int],
    attachment_counts: dict[str, int],
    run_counts: dict[str, int],
    latest_runs: dict[str, sqlite3.Row],
) -> dict[str, Any]:
    task_id = str(row["id"])
    title = str(row["title"] or "")
    latest_run = latest_runs.get(task_id)
    task = {
        "event_id": f"hermes-kanban-task:{board}:{task_id}",
        "board": board,
        "task_id": task_id,
        "title": title[:240] if include_title else None,
        "title_hash": hashlib.sha256(title.encode("utf-8")).hexdigest() if title else None,
        "assignee": str(row["assignee"] or "") or None,
        "status": str(row["status"] or "unknown"),
        "priority": int(row["priority"] or 0),
        "created_by": str(row["created_by"] or "") or None,
        "created_at": int_to_iso(row["created_at"]),
        "started_at": int_to_iso(row["started_at"]),
        "completed_at": int_to_iso(row["completed_at"]),
        "workspace_kind": str(row["workspace_kind"] or "") or None,
        "workspace_present": bool_int(row["workspace_path"]),
        "project_id": str(row["project_id"] or "") or None,
        "tenant": str(row["tenant"] or "") or None,
        "result_present": bool_int(row["result"]),
        "idempotency_key": str(row["idempotency_key"] or "") or None,
        "consecutive_failures": int(row["consecutive_failures"] or 0),
        "worker_pid_present": bool_int(row["worker_pid"]),
        "last_failure_present": bool_int(row["last_failure_error"]),
        "max_runtime_seconds": int(row["max_runtime_seconds"] or 0) or None,
        "last_heartbeat_at": int_to_iso(row["last_heartbeat_at"]),
        "current_run_id": int(row["current_run_id"] or 0) or None,
        "workflow_template_id": str(row["workflow_template_id"] or "") or None,
        "current_step_key": str(row["current_step_key"] or "") or None,
        "skill_count": json_size(row["skills"]),
        "model_override": str(row["model_override"] or "") or None,
        "max_retries": int(row["max_retries"] or 0) or None,
        "goal_mode": bool(row["goal_mode"] or 0),
        "goal_max_turns": int(row["goal_max_turns"] or 0) or None,
        "session_id": str(row["session_id"] or "") or None,
        "block_kind": str(row["block_kind"] or "") or None,
        "block_recurrences": int(row["block_recurrences"] or 0),
        "parent_count": dependency_counts.get(task_id, 0),
        "child_count": child_counts.get(task_id, 0),
        "comment_count": comment_counts.get(task_id, 0),
        "attachment_count": attachment_counts.get(task_id, 0),
        "run_count": run_counts.get(task_id, 0),
        "latest_run_id": int(latest_run["id"] or 0) if latest_run else None,
        "latest_run_status": str(latest_run["status"] or "") if latest_run else None,
        "latest_run_outcome": str(latest_run["outcome"] or "") if latest_run else None,
        "latest_run_started_at": int_to_iso(latest_run["started_at"]) if latest_run else None,
        "latest_run_ended_at": int_to_iso(latest_run["ended_at"]) if latest_run else None,
    }
    return task


def normalize_kanban_run(board: str, row: sqlite3.Row) -> dict[str, Any]:
    return {
        "event_id": f"hermes-kanban-run:{board}:{int(row['id'])}",
        "board": board,
        "run_id": int(row["id"]),
        "task_id": str(row["task_id"]),
        "profile": str(row["profile"] or "") or None,
        "step_key": str(row["step_key"] or "") or None,
        "status": str(row["status"] or "unknown"),
        "claim_lock_present": bool_int(row["claim_lock"]),
        "claim_expires_at": int_to_iso(row["claim_expires"]),
        "worker_pid_present": bool_int(row["worker_pid"]),
        "max_runtime_seconds": int(row["max_runtime_seconds"] or 0) or None,
        "last_heartbeat_at": int_to_iso(row["last_heartbeat_at"]),
        "started_at": int_to_iso(row["started_at"]),
        "ended_at": int_to_iso(row["ended_at"]),
        "outcome": str(row["outcome"] or "") or None,
        "summary_present": bool_int(row["summary"]),
        "metadata_keys": json_keys(row["metadata"]),
        "error_present": bool_int(row["error"]),
    }


def normalize_kanban_event(board: str, row: sqlite3.Row) -> dict[str, Any]:
    return {
        "event_id": f"hermes-kanban-event:{board}:{int(row['id'])}",
        "board": board,
        "source_event_id": int(row["id"]),
        "task_id": str(row["task_id"]),
        "run_id": int(row["run_id"] or 0) or None,
        "kind": str(row["kind"] or "unknown"),
        "payload_present": bool_int(row["payload"]),
        "payload_keys": json_keys(row["payload"]),
        "created_at": int_to_iso(row["created_at"]),
    }


def normalize_kanban_link(board: str, row: sqlite3.Row) -> dict[str, Any]:
    return {
        "event_id": f"hermes-kanban-link:{board}:{row['parent_id']}:{row['child_id']}",
        "board": board,
        "parent_id": str(row["parent_id"]),
        "child_id": str(row["child_id"]),
    }


def collect_kanban_changes(settings: Settings, state: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    changes = {
        "kanban_tasks": [],
        "kanban_task_runs": [],
        "kanban_task_events": [],
        "kanban_task_links": [],
    }
    state_kanban = state.setdefault("kanban", {})
    boards_seen: list[str] = []

    for board, db_path in discover_kanban_databases(settings.hermes_home):
        boards_seen.append(board)
        board_state = state_kanban.setdefault(board, {})
        board_state.setdefault("tasks", {})
        board_state.setdefault("runs", {})
        board_state.setdefault("events", {})
        board_state.setdefault("links", {})

        try:
            connection = sqlite_readonly(db_path)
            connection.row_factory = sqlite3.Row
            task_rows = select_all(connection, "tasks")
            run_rows = select_all(connection, "task_runs")
            event_rows = select_all(connection, "task_events")
            link_rows = select_all(connection, "task_links")

            dependency_counts = {
                str(row["child_id"]): int(row["count"])
                for row in connection.execute(
                    "SELECT child_id, count(*) AS count FROM task_links GROUP BY child_id"
                )
            } if link_rows else {}
            child_counts = {
                str(row["parent_id"]): int(row["count"])
                for row in connection.execute(
                    "SELECT parent_id, count(*) AS count FROM task_links GROUP BY parent_id"
                )
            } if link_rows else {}
            comment_counts = count_by_task(connection, "task_comments")
            attachment_counts = count_by_task(connection, "task_attachments")
            run_counts = count_by_task(connection, "task_runs")
            latest_runs = latest_runs_by_task(connection)
            connection.close()
        except (sqlite3.Error, OSError) as exc:
            print(f"telemetry: cannot read {board} kanban database: {exc}", file=sys.stderr)
            continue

        for row in task_rows:
            item = normalize_kanban_task(
                board,
                row,
                settings.include_kanban_titles,
                dependency_counts,
                child_counts,
                comment_counts,
                attachment_counts,
                run_counts,
                latest_runs,
            )
            digest = fingerprint(item)
            key = item["task_id"]
            if board_state["tasks"].get(key, {}).get("fingerprint") != digest:
                changes["kanban_tasks"].append({**item, "fingerprint": digest})
            board_state["tasks"][key] = {"fingerprint": digest}

        for row in run_rows:
            item = normalize_kanban_run(board, row)
            digest = fingerprint(item)
            key = str(item["run_id"])
            if board_state["runs"].get(key, {}).get("fingerprint") != digest:
                changes["kanban_task_runs"].append({**item, "fingerprint": digest})
            board_state["runs"][key] = {"fingerprint": digest}

        for row in event_rows:
            item = normalize_kanban_event(board, row)
            digest = fingerprint(item)
            key = str(item["source_event_id"])
            if board_state["events"].get(key, {}).get("fingerprint") != digest:
                changes["kanban_task_events"].append({**item, "fingerprint": digest})
            board_state["events"][key] = {"fingerprint": digest}

        for row in link_rows:
            item = normalize_kanban_link(board, row)
            digest = fingerprint(item)
            key = f"{item['parent_id']}:{item['child_id']}"
            if board_state["links"].get(key, {}).get("fingerprint") != digest:
                changes["kanban_task_links"].append({**item, "fingerprint": digest})
            board_state["links"][key] = {"fingerprint": digest}

    state["kanban_boards_seen"] = boards_seen
    return changes


def collect_changes(settings: Settings, state: dict[str, Any], now: float) -> list[dict[str, Any]]:
    changes: list[dict[str, Any]] = []
    state_runs = state.setdefault("runs", {})
    profiles_seen: list[str] = []

    for profile, db_path in discover_databases(
        settings.hermes_home, settings.excluded_profiles
    ):
        profiles_seen.append(profile)
        profile_state = state_runs.setdefault(profile, {})
        tracked_open_ids = [
            session_id
            for session_id, saved in profile_state.items()
            if isinstance(saved, dict) and saved.get("status") == "open"
        ]
        initial = not profile_state
        hours = settings.initial_backfill_hours if initial else settings.rescan_hours
        cutoff = now - hours * 3600

        try:
            connection = sqlite_readonly(db_path)
            rows = select_sessions(connection, cutoff, tracked_open_ids)
            usage = collect_tool_usage(connection, [str(row["id"]) for row in rows])
            connection.close()
        except (sqlite3.Error, OSError) as exc:
            print(f"telemetry: cannot read {profile} database: {exc}", file=sys.stderr)
            continue

        for row in rows:
            session_id = str(row["id"])
            run = normalize_session(profile, row, usage.get(session_id, {}))
            digest = fingerprint(run)
            previous = profile_state.get(session_id, {})
            if previous.get("fingerprint") != digest:
                changes.append({**run, "fingerprint": digest})
            profile_state[session_id] = {
                "fingerprint": digest,
                "status": run["status"],
                "last_seen_at": utc_iso(now),
            }

    state["profiles_seen"] = profiles_seen
    state["updated_at"] = utc_iso(now)
    return changes


def collect_goal_changes(settings: Settings, state: dict[str, Any]) -> list[dict[str, Any]]:
    changes: list[dict[str, Any]] = []
    state_goals = state.setdefault("goals", {})

    for profile, db_path in discover_databases(
        settings.hermes_home, settings.excluded_profiles
    ):
        profile_state = state_goals.setdefault(profile, {})
        try:
            connection = sqlite_readonly(db_path)
            goal_rows = select_goal_states(connection)
            session_columns = table_columns(connection, "sessions")
            model_expression = "model" if "model" in session_columns else "'unknown' AS model"
            session_models = (
                {
                    str(row["id"]): str(row["model"] or "unknown")
                    for row in connection.execute(f"SELECT id, {model_expression} FROM sessions")
                }
                if "id" in session_columns
                else {}
            )
            connection.close()
        except (sqlite3.Error, OSError) as exc:
            print(f"telemetry: cannot read {profile} goal states: {exc}", file=sys.stderr)
            continue

        goal_runs: dict[str, dict[str, Any]] = {}
        status_priority = {"done": 4, "paused": 3, "active": 2, "cleared": 1}
        for row in goal_rows:
            goal_run = normalize_goal_run(profile, row, session_models)
            if goal_run is None:
                continue
            event_id = goal_run["event_id"]
            current = goal_runs.get(event_id)
            if current is None or status_priority.get(goal_run["status"], 0) > status_priority.get(
                current["status"], 0
            ):
                goal_runs[event_id] = goal_run

        for event_id, goal_run in goal_runs.items():
            digest = fingerprint(goal_run)
            if profile_state.get(event_id, {}).get("fingerprint") != digest:
                changes.append({**goal_run, "fingerprint": digest})
            profile_state[event_id] = {
                "fingerprint": digest,
                "status": goal_run["status"],
            }

    return changes


ORCA_CALL_STATE_TTL_SECONDS = 14 * 24 * 3600


def _orca_calls_module():
    """Import orca_calls lazily so collector.py stays importable on its own."""
    here = str(Path(__file__).resolve().parent)
    if here not in sys.path:
        sys.path.insert(0, here)
    import orca_calls

    return orca_calls


def collect_orca_call_changes(
    settings: Settings,
    state: dict[str, Any],
    now: float | None = None,
) -> list[dict[str, Any]]:
    """Per-call ORCA usage events (metadata only), changed since the last delivery.

    Bounded: only files modified inside the lookback window are read, and at most
    `orca_call_batch_limit` events are returned per run. Events beyond the limit are
    not recorded in state, so they are picked up on a later run. A file is marked done
    only when every changed event in it was included. State is saved by the caller
    only after delivery succeeds.
    """
    if not settings.collect_orca_calls:
        return []
    orca_calls = _orca_calls_module()
    now = time.time() if now is None else now
    cutoff = now - settings.orca_call_lookback_hours * 3600
    sent = state.setdefault("orca_calls", {})
    files_done = state.setdefault("orca_call_files", {})

    def in_scope(cwd: Any) -> bool:
        return is_orca_worktree(cwd, settings.orca_worktree_roots)

    candidates: list[tuple[float, str, Path]] = []
    for agent, root in (("codex", settings.orca_codex_sessions_dir), ("claude", settings.orca_claude_projects_dir)):
        if root is None or not root.is_dir():
            continue
        for path in root.rglob("*.jsonl"):
            try:
                stat = path.stat()
            except OSError:
                continue
            signature = [int(stat.st_mtime), stat.st_size]
            if stat.st_mtime < cutoff or files_done.get(str(path)) == signature:
                continue
            candidates.append((stat.st_mtime, agent, path))

    changes: list[dict[str, Any]] = []
    for _, agent, path in sorted(candidates, key=lambda item: item[0]):
        try:
            stat = path.stat()
            with path.open(encoding="utf-8", errors="replace") as handle:
                events, _rejected = orca_calls.extract_call_events(
                    agent, handle, settings.orca_origin_namespace, in_scope
                )
        except OSError as exc:
            print(f"telemetry: cannot read ORCA {agent} log for call events: {exc}", file=sys.stderr)
            continue
        pending = []
        for event in events:
            digest = fingerprint(event)
            if sent.get(event["event_key"], [None])[0] != digest[:16]:
                pending.append((event, digest))
        room = settings.orca_call_batch_limit - len(changes)
        for event, digest in pending[:room]:
            changes.append({**event, "fingerprint": digest})
            sent[event["event_key"]] = [digest[:16], int(now)]
        if len(pending) > room:
            break  # file not marked done; the remaining events go out on later runs
        files_done[str(path)] = [int(stat.st_mtime), stat.st_size]
        if len(changes) >= settings.orca_call_batch_limit:
            break
    for key in [k for k, v in sent.items() if now - v[1] > ORCA_CALL_STATE_TTL_SECONDS]:
        del sent[key]
    for key in [k for k, v in files_done.items() if v[0] < cutoff - ORCA_CALL_STATE_TTL_SECONDS]:
        del files_done[key]
    return changes


def _hermes_sessions_module():
    here = str(Path(__file__).resolve().parent)
    if here not in sys.path:
        sys.path.insert(0, here)
    import hermes_sessions

    return hermes_sessions


HERMES_SESSION_STATE_TTL_SECONDS = 30 * 24 * 3600


def collect_hermes_session_changes(
    settings: Settings, state: dict[str, Any], now: float,
) -> dict[str, list[dict[str, Any]]]:
    """Session samples (cumulative counters, sent only when something changed) and tool events.

    Hermes exposes no per-call token counts, so usage over time is the difference between
    consecutive samples. State is only saved by the caller after delivery succeeds.
    """
    empty: dict[str, list[dict[str, Any]]] = {"hermes_session_samples": [], "hermes_tool_events": [], "hermes_tool_log_events": []}
    if not settings.collect_hermes_sessions:
        return empty
    hs = _hermes_sessions_module()
    orca_calls = _orca_calls_module()
    sessions_state = state.setdefault("hermes_sessions", {})
    cursors = state.setdefault("hermes_tool_cursor", {})
    if state.get("hermes_outcome_rules") != hs.OUTCOME_RULES_VERSION:
        cursors.clear()  # classification rules changed: re-send tool events so stored outcomes are refreshed
        state["hermes_outcome_rules"] = hs.OUTCOME_RULES_VERSION
    cutoff = now - settings.hermes_session_lookback_hours * 3600
    log_offsets = state.setdefault("hermes_log_offsets", {})
    result = {"hermes_session_samples": [], "hermes_tool_events": [], "hermes_tool_log_events": []}
    for profile, db_path in discover_databases(settings.hermes_home, settings.excluded_profiles):
        profile_state = sessions_state.setdefault(profile, {})
        still_open = [sid for sid, saved in profile_state.items() if saved[2]]
        try:
            connection = sqlite_readonly(db_path)
            samples = hs.session_samples(connection, profile, cutoff, now, still_open)
            events = hs.tool_events(
                connection, profile, int(cursors.get(profile, 0)), cutoff,
                settings.hermes_tool_batch_limit - len(result["hermes_tool_events"]), orca_calls.tool_label,
            )
            connection.close()
        except (sqlite3.Error, OSError) as exc:
            print(f"telemetry: cannot read {profile} database for session samples: {exc}", file=sys.stderr)
            continue
        for sample in samples:
            fp = hs.sample_fingerprint(sample)[:32]
            sid = sample["_session_id"]
            if profile_state.get(sid, [None])[0] == fp:
                profile_state[sid][1] = int(now)
                continue
            result["hermes_session_samples"].append(hs.finalize_sample(sample, hs.sample_fingerprint(sample), now))
            profile_state[sid] = [fp, int(now), sample["ended_at"] is None and not sample["expiry_finalized"]]
        for event in events:
            cursors[profile] = max(int(cursors.get(profile, 0)), event.pop("_message_id"))
            event["fingerprint"] = hs.digest(event["event_key"], event["outcome"], size=32)
            result["hermes_tool_events"].append(event)
        for event in hs.read_tool_log_events(
            db_path.parent / "logs", profile, log_offsets.setdefault(profile, {}), cutoff,
            settings.hermes_tool_batch_limit - len(result["hermes_tool_log_events"]), orca_calls.tool_label,
        ):
            event.pop("_ts")
            event["fingerprint"] = event["event_key"]
            result["hermes_tool_log_events"].append(event)
        for sid in [s for s, v in profile_state.items() if not v[2] and now - v[1] > HERMES_SESSION_STATE_TTL_SECONDS]:
            del profile_state[sid]
    return result


def collect_payload_changes(settings: Settings, state: dict[str, Any], now: float) -> dict[str, list[dict[str, Any]]]:
    payload_changes = {
        "runs": collect_changes(settings, state, now),
        "goal_runs": collect_goal_changes(settings, state),
        "orca_usage": collect_orca_usage_changes(settings, state, now),
        "orca_thread_usage": collect_orca_thread_usage_changes(settings, state, now),
        "orca_call_events": collect_orca_call_changes(settings, state, now),
    }
    payload_changes.update(collect_kanban_changes(settings, state))
    payload_changes.update(collect_hermes_session_changes(settings, state, now))
    return payload_changes


def post_batch(settings: Settings, payload_changes: dict[str, list[dict[str, Any]]], observed_at: str) -> None:
    payload = {
        "schema_version": SCHEMA_VERSION,
        "collector_id": settings.collector_id,
        "observed_at": observed_at,
        **payload_changes,
    }
    headers = {
        "Content-Type": "application/json",
        "User-Agent": "hermes-telemetry-collector/1",
        "X-Hermes-Telemetry-Schema": str(SCHEMA_VERSION),
    }
    if settings.webhook_token:
        headers["Authorization"] = f"Bearer {settings.webhook_token}"
    request = Request(
        settings.webhook_url,
        data=json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8"),
        headers=headers,
        method="POST",
    )
    context = None if settings.verify_tls else ssl._create_unverified_context()
    with urlopen(request, timeout=settings.request_timeout_seconds, context=context) as response:
        if not 200 <= response.status < 300:
            raise RuntimeError(f"webhook returned HTTP {response.status}")


def run_once(settings: Settings, dry_run: bool = False) -> int:
    if not dry_run and not settings.webhook_url:
        print("telemetry: HERMES_TELEMETRY_WEBHOOK_URL is required", file=sys.stderr)
        return 2

    state = load_state(settings.state_path)
    now = time.time()
    changes = collect_payload_changes(settings, state, now)
    observed_at = utc_iso(now) or ""

    if dry_run:
        print(
            json.dumps(
                {
                    "schema_version": SCHEMA_VERSION,
                    "collector_id": settings.collector_id,
                    "observed_at": observed_at,
                    **changes,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0

    try:
        for entity_name, items in changes.items():
            for start in range(0, len(items), settings.batch_size):
                post_batch(settings, {entity_name: items[start : start + settings.batch_size]}, observed_at)
    except (HTTPError, URLError, TimeoutError, RuntimeError, OSError) as exc:
        print(f"telemetry: webhook delivery failed: {exc}", file=sys.stderr)
        return 1

    save_state(settings.state_path, state)
    counts = ", ".join(f"{name}={len(items)}" for name, items in sorted(changes.items()))
    print(f"telemetry: delivered changed telemetry ({counts})")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--once", action="store_true", help="collect and deliver once, then exit")
    parser.add_argument("--dry-run", action="store_true", help="print a privacy-safe payload without sending")
    args = parser.parse_args()
    settings = Settings.from_env()

    if args.once or args.dry_run or settings.interval_seconds == 0:
        return run_once(settings, dry_run=args.dry_run)

    while True:
        run_once(settings)
        time.sleep(settings.interval_seconds)


if __name__ == "__main__":
    raise SystemExit(main())
