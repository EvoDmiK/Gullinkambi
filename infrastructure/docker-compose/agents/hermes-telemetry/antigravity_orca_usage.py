#!/usr/bin/env python3
"""Write privacy-safe ORCA Antigravity token aggregates from local CLI metadata."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sqlite3
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse


MODEL_PATTERN = re.compile(
    rb"(?:gemini|claude|gpt)-[a-z0-9][a-z0-9._-]{1,126}", re.IGNORECASE
)
USAGE_TIMEZONE = timezone(timedelta(hours=9))


def protobuf_fields(data: bytes) -> list[tuple[int, int, int | bytes]]:
    fields: list[tuple[int, int, int | bytes]] = []
    offset = 0

    def read_varint() -> int:
        nonlocal offset
        value = 0
        shift = 0
        while offset < len(data) and shift < 70:
            byte = data[offset]
            offset += 1
            value |= (byte & 0x7F) << shift
            if byte < 0x80:
                return value
            shift += 7
        raise ValueError("invalid protobuf varint")

    while offset < len(data):
        key = read_varint()
        field_number = key >> 3
        wire_type = key & 0x07
        if field_number == 0:
            raise ValueError("invalid protobuf field number")
        if wire_type == 0:
            value: int | bytes = read_varint()
        elif wire_type == 1:
            end = offset + 8
            if end > len(data):
                raise ValueError("truncated protobuf fixed64")
            value = int.from_bytes(data[offset:end], "little")
            offset = end
        elif wire_type == 2:
            size = read_varint()
            end = offset + size
            if end > len(data):
                raise ValueError("truncated protobuf bytes")
            value = data[offset:end]
            offset = end
        elif wire_type == 5:
            end = offset + 4
            if end > len(data):
                raise ValueError("truncated protobuf fixed32")
            value = int.from_bytes(data[offset:end], "little")
            offset = end
        else:
            raise ValueError(f"unsupported protobuf wire type: {wire_type}")
        fields.append((field_number, wire_type, value))
    return fields


def parse_step_metadata(data: bytes) -> tuple[float, dict[str, int]] | None:
    try:
        fields = protobuf_fields(data)
    except ValueError:
        return None

    timestamp: float | None = None
    usage: dict[str, int] | None = None
    for field_number, wire_type, value in fields:
        if wire_type != 2 or not isinstance(value, bytes):
            continue
        if field_number == 1:
            try:
                parts = {
                    number: int(part)
                    for number, part_wire, part in protobuf_fields(value)
                    if part_wire == 0 and isinstance(part, int)
                }
            except ValueError:
                continue
            seconds = parts.get(1)
            if seconds and seconds >= 1_500_000_000:
                timestamp = seconds + parts.get(2, 0) / 1_000_000_000
        elif field_number == 9:
            try:
                parts = {
                    number: int(part)
                    for number, part_wire, part in protobuf_fields(value)
                    if part_wire == 0 and isinstance(part, int)
                }
            except ValueError:
                continue
            if 2 in parts and 3 in parts:
                input_tokens = max(0, parts.get(2, 0))
                output_tokens = max(0, parts.get(3, 0))
                usage = {
                    "inputTokens": input_tokens,
                    "cacheReadTokens": max(0, parts.get(5, 0)),
                    "cacheWriteTokens": 0,
                    "outputTokens": output_tokens,
                    "reasoningTokens": max(0, parts.get(9, 0)),
                    "totalTokens": input_tokens + output_tokens,
                }
    if timestamp is None or usage is None:
        return None
    return timestamp, usage


def model_name(data: bytes) -> str | None:
    match = MODEL_PATTERN.search(data)
    return match.group(0).decode("ascii")[:128] if match else None


def readonly_connection(path: Path) -> sqlite3.Connection:
    # Antigravity databases use WAL mode. immutable avoids SQLite trying to
    # create shared-memory files while this short-lived, read-only scan runs.
    return sqlite3.connect(
        f"{path.resolve().as_uri()}?mode=ro&immutable=1", uri=True, timeout=2
    )


def under_root(path: Path, roots: tuple[Path, ...]) -> bool:
    if not path.is_absolute():
        return False
    for root in roots:
        try:
            path.relative_to(root)
            return True
        except ValueError:
            continue
    return False


def workspace_path(value: Any, roots: tuple[Path, ...]) -> Path | None:
    try:
        uris = json.loads(str(value or "[]"))
    except json.JSONDecodeError:
        return None
    if not isinstance(uris, list):
        return None
    for raw_uri in uris:
        parsed = urlparse(str(raw_uri))
        if parsed.scheme != "file" or parsed.netloc not in ("", "localhost"):
            continue
        path = Path(unquote(parsed.path))
        if under_root(path, roots):
            return path
    return None


def safe_hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:24]


def project_label_for(cwd: Path) -> str:
    # Match the collector: label ORCA worktrees by repository, not by branch folder
    # (…/orca/[…/]workspaces/<repo>/<branch> or …/orca/<repo>/…).
    parts = cwd.parts
    for index, part in enumerate(parts):
        if part == "orca":
            rest = parts[index + 1:]
            project_index = rest.index("workspaces") + 1 if "workspaces" in rest else 0
            if project_index < len(rest):
                return rest[project_index][:128]
    return (cwd.name or "unknown")[:128]


def thread_name(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    name = " ".join(value.split())[:160]
    return name or None


def empty_aggregate(day: str, model: str, project_hash: str, project_label: str) -> dict[str, Any]:
    return {
        "day": day,
        "model": model,
        "projectKey": project_hash,
        "projectLabel": project_label,
        "worktreeId": project_hash,
        "requestCount": 0,
        "inputTokens": 0,
        "cacheReadTokens": 0,
        "cacheWriteTokens": 0,
        "outputTokens": 0,
        "reasoningTokens": 0,
        "totalTokens": 0,
    }


def add_usage(target: dict[str, Any], usage: dict[str, int]) -> None:
    target["requestCount"] += 1
    for field in (
        "inputTokens",
        "cacheReadTokens",
        "cacheWriteTokens",
        "outputTokens",
        "reasoningTokens",
        "totalTokens",
    ):
        target[field] += usage[field]


def conversation_usage(path: Path) -> list[tuple[float, str, dict[str, int]]]:
    with readonly_connection(path) as connection:
        models = [
            model_name(bytes(data)) if isinstance(data, bytes) else None
            for (data,) in connection.execute("SELECT data FROM gen_metadata ORDER BY idx")
        ]
        metadata_rows = connection.execute(
            "SELECT metadata FROM steps WHERE metadata IS NOT NULL ORDER BY idx"
        ).fetchall()

    default_model = next((model for model in models if model), "antigravity")
    current_model = default_model
    result: list[tuple[float, str, dict[str, int]]] = []
    usage_index = 0
    for (metadata,) in metadata_rows:
        if not isinstance(metadata, bytes):
            continue
        parsed = parse_step_metadata(metadata)
        if parsed is None:
            continue
        if usage_index < len(models) and models[usage_index]:
            current_model = str(models[usage_index])
        timestamp, usage = parsed
        result.append((timestamp, current_model, usage))
        usage_index += 1
    return result


def build_document(antigravity_dir: Path, roots: tuple[Path, ...]) -> dict[str, Any]:
    summaries_path = antigravity_dir / "conversation_summaries.db"
    conversations_dir = antigravity_dir / "conversations"
    aggregates: dict[tuple[str, str, str], dict[str, Any]] = {}
    thread_aggregates: dict[tuple[str, str, str, str], dict[str, Any]] = {}

    with readonly_connection(summaries_path) as connection:
        columns = {row[1] for row in connection.execute("PRAGMA table_info(conversation_summaries)")}
        title_column = "title" if "title" in columns else "NULL"
        summaries = connection.execute(
            f"SELECT conversation_id, workspace_uris, {title_column} FROM conversation_summaries"
        ).fetchall()

    for conversation_id, workspace_uris, title in summaries:
        identifier = str(conversation_id or "")
        if re.fullmatch(r"[A-Za-z0-9-]{8,128}", identifier) is None:
            continue
        cwd = workspace_path(workspace_uris, roots)
        if cwd is None:
            continue
        path = conversations_dir / f"{identifier}.db"
        if not path.is_file():
            continue
        try:
            usage_rows = conversation_usage(path)
        except sqlite3.Error as exc:
            print(f"antigravity usage: skipped {identifier}: {exc}", file=sys.stderr)
            continue

        project_hash = safe_hash(str(cwd))
        project_label = project_label_for(cwd)
        # The conversation ID never leaves this machine; threads are keyed by its hash.
        thread_id = safe_hash(f"antigravity:{identifier}")
        name = thread_name(title)
        for timestamp, model, usage in usage_rows:
            day = (
                datetime.fromtimestamp(timestamp, tz=timezone.utc)
                .astimezone(USAGE_TIMEZONE)
                .date()
                .isoformat()
            )
            key = (day, model, project_hash)
            if key not in aggregates:
                aggregates[key] = empty_aggregate(day, model, project_hash, project_label)
            add_usage(aggregates[key], usage)

            thread_key = (day, model, project_hash, thread_id)
            if thread_key not in thread_aggregates:
                thread_aggregates[thread_key] = {
                    **empty_aggregate(day, model, project_hash, project_label),
                    "threadId": thread_id,
                    "threadName": name,
                }
            add_usage(thread_aggregates[thread_key], usage)

    return {
        "schemaVersion": 2,
        "generatedAt": datetime.now(tz=timezone.utc).isoformat().replace("+00:00", "Z"),
        "dailyAggregates": [aggregates[key] for key in sorted(aggregates)],
        "threadAggregates": [thread_aggregates[key] for key in sorted(thread_aggregates)],
    }


def write_in_place(path: Path, document: dict[str, Any]) -> None:
    """Overwrite the aggregate file keeping its inode.

    The telemetry container bind-mounts this single file; a rename-based
    replace would leave the container looking at a deleted inode. The collector
    skips unreadable or partial JSON and retries on its next cycle.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(document, ensure_ascii=False, separators=(",", ":")) + "\n"
    with path.open("w", encoding="utf-8") as handle:
        handle.write(payload)
        handle.flush()
        os.fsync(handle.fileno())
    os.chmod(path, 0o600)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--antigravity-dir",
        type=Path,
        default=Path.home() / ".gemini" / "antigravity-cli",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path.home()
        / "Library"
        / "Application Support"
        / "orca"
        / "orca-antigravity-usage.json",
    )
    parser.add_argument("--worktree-root", type=Path, action="append", default=[])
    args = parser.parse_args()
    roots = tuple(
        args.worktree_root
        or [Path.home() / "orca", Path("/Volumes/Example/orca")]
    )
    document = build_document(args.antigravity_dir, roots)
    write_in_place(args.output, document)
    print(f"wrote {len(document['dailyAggregates'])} Antigravity aggregate(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
