"""Per-call ORCA usage normalization (metadata only, no prompt/response text).

Provider token semantics differ, so normalized values are separate from the
legacy ``total_tokens`` columns. Unknown values stay ``None`` with a reason;
they are never coerced to 0.
"""
from __future__ import annotations

import collections
import hashlib
import json
import re
from typing import Any, Iterable

NORMALIZATION_VERSION = 1
PROVIDERS = ("codex", "claude")

# A field is known (int), unknown (None + reason), or not separately observable.
NOT_SEPARATE = "not_separately_observable"


class InvalidUsage(ValueError):
    """Raised for schema-invariant violations; callers quarantine the record."""


def _count(usage: dict[str, Any], key: str) -> int | None:
    value = usage.get(key)
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise InvalidUsage(f"{key}_not_integer")
    if value < 0:
        raise InvalidUsage(f"{key}_negative")
    return value


def _add(*values: int | None) -> int | None:
    return None if any(v is None for v in values) else sum(values)  # type: ignore[arg-type]


def normalize_call(agent: str, usage: dict[str, Any]) -> dict[str, Any]:
    """Normalize one provider usage record into the shared per-call fields."""
    if agent not in PROVIDERS:
        raise InvalidUsage("unsupported_provider")
    unknown: dict[str, str] = {}

    if agent == "codex":
        inp = _count(usage, "input_tokens")
        out = _count(usage, "output_tokens")
        cache_read = _count(usage, "cached_input_tokens")
        reasoning = _count(usage, "reasoning_output_tokens")
        if inp is not None and cache_read is not None and cache_read > inp:
            raise InvalidUsage("cached_input_exceeds_input")
        cache_write = _count(usage, "cache_write_input_tokens")
        uncached = None if inp is None or cache_read is None else inp - cache_read
        normalized_input = inp  # Codex input already includes cached input
        non_cache = _add(uncached, out)
        cache_write_state = NOT_SEPARATE if "cache_write_input_tokens" not in usage else None
        if cache_write:
            # Whether input_tokens already contains cache writes is unverified.
            non_cache = None
            unknown["non_cache_tokens"] = "codex_cache_write_semantics_unverified"
    else:
        uncached = _count(usage, "input_tokens")
        out = _count(usage, "output_tokens")
        cache_read = _count(usage, "cache_read_input_tokens")
        cache_write = _count(usage, "cache_creation_input_tokens")
        reasoning = None
        normalized_input = _add(uncached, cache_read, cache_write)
        non_cache = _add(uncached, cache_write, out)
        cache_write_state = None

    processed_total = _add(normalized_input, out)  # reasoning is an output subset
    row = {
        "agent": agent,
        "normalization_version": NORMALIZATION_VERSION,
        "uncached_input_tokens": uncached,
        "cache_read_tokens": cache_read,
        "cache_write_tokens": cache_write,
        "output_tokens": out,
        "reasoning_tokens_subset": reasoning,
        "normalized_input_tokens": normalized_input,
        "processed_total_tokens": processed_total,
        "non_cache_tokens": non_cache,
    }
    if cache_write_state:
        unknown["cache_write_tokens"] = cache_write_state
    for name, value in row.items():
        if value is None and name not in unknown and name not in (
            "agent", "normalization_version",
        ):
            unknown[name] = "missing_in_source"
    row["unknown_fields"] = unknown
    return row


def event_key(agent: str, origin_namespace: str, session_id: str, request_id: str) -> str:
    """Stable call identity; excludes file path, collector id and observation time."""
    raw = "\x1f".join((agent, origin_namespace, session_id, request_id))
    return "orca-call:" + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]


def aggregate(rows: Iterable[dict[str, Any]]) -> dict[str, Any]:
    """Sum canonical rows with explicit coverage; never present a partial sum as total."""
    rows = [r for r in rows if r.get("normalization_version") == NORMALIZATION_VERSION]
    eligible = len(rows)

    def known_sum(field: str) -> tuple[int, int]:
        values = [r[field] for r in rows if r.get(field) is not None]
        return sum(values), len(values)

    processed, processed_n = known_sum("processed_total_tokens")
    non_cache, non_cache_n = known_sum("non_cache_tokens")
    cohort = [
        r for r in rows
        if r.get("cache_read_tokens") is not None
        and r.get("normalized_input_tokens") is not None
    ]
    denominator = sum(r["normalized_input_tokens"] for r in cohort)
    hit_rate = (
        sum(r["cache_read_tokens"] for r in cohort) / denominator * 100
        if denominator
        else None
    )

    def coverage(known: int) -> str:
        if known == eligible:
            return "complete"
        return "unknown" if known == 0 else "partial"

    return {
        "eligible_count": eligible,
        "processed_total": {
            "known_sum": processed, "known_count": processed_n,
            "complete_total": processed if processed_n == eligible else None,
            "coverage_state": coverage(processed_n),
        },
        "non_cache": {
            "known_sum": non_cache, "known_count": non_cache_n,
            "complete_total": non_cache if non_cache_n == eligible else None,
            "coverage_state": coverage(non_cache_n),
        },
        "cache_hit_rate_percent": hit_rate,
        "cache_hit_cohort_count": len(cohort),
    }


def _hash(value: Any) -> str:
    return hashlib.sha256(str(value or "unknown").encode("utf-8")).hexdigest()[:24]


TOOL_NAME_RE = re.compile(r"[A-Za-z][A-Za-z0-9_.:\-]{0,63}")
MAX_TOOL_NAMES = 40  # per request; the rest is folded into "other"


def tool_label(name: Any, namespace: Any = None) -> str:
    """Safe, low-cardinality tool name. Arguments are never read; MCP tools keep only the server.

    Anything that is not a plain identifier becomes "other", so no free text can pass through.
    """
    if isinstance(namespace, str) and namespace.startswith("mcp__"):
        name = namespace
    if not isinstance(name, str) or not name:
        return "other"
    if name.startswith("mcp__"):
        server = name.split("__")[1] if len(name.split("__")) > 1 else ""
        name = f"mcp:{server}" if server else "mcp:unknown"
    return name if TOOL_NAME_RE.fullmatch(name) else "other"


def _tools_field(names: Iterable[str]) -> dict[str, int]:
    counts = collections.Counter(names)
    if len(counts) > MAX_TOOL_NAMES:
        top = dict(counts.most_common(MAX_TOOL_NAMES - 1))
        top["other"] = top.get("other", 0) + sum(counts.values()) - sum(top.values())
        return top
    return dict(counts)


def _call_event(agent, namespace, session_id, request_id, model, cwd, timestamp, usage, final, seq):
    row = normalize_call(agent, usage)
    row.update({
        "schema_version": 1,
        "event_key": event_key(agent, namespace, session_id, request_id),
        "origin_namespace": namespace,
        "session_id_hash": _hash(session_id),
        "source_request_id_hash": _hash(request_id),
        "model": str(model or "unknown")[:128],
        "project_key": _hash(cwd),
        "origin_evidence": "path_only",
        "source_kind": "raw_call",
        "occurred_at": timestamp,
        "event_status": "final" if final else "partial",
        "revision": seq,
        # None = not observed (never coerced to 0); filled in by extract_call_events
        "tool_calls": None, "tool_calls_total": None, "tool_failures": None,
    })
    return row


def extract_call_events(agent: str, lines: Iterable[str], namespace: str, in_scope) -> tuple[list[dict[str, Any]], int]:
    """Parse one provider log into per-call events; returns (events, rejected_count).

    Partial updates of the same request collapse to the final revision; if no
    final line was seen the latest one is kept as ``partial``. Truncated JSON
    lines are skipped so an in-progress append is retried on the next pass.
    ``in_scope(cwd)`` decides ORCA membership (path evidence only).

    Tool use is counted by name only. Claude: tool_use blocks are collected across every line of
    a request and failures come from the matching tool_result (`is_error`); a late result raises
    the event's revision so the database accepts the update. Codex: tool calls carry no response
    id, so the calls seen since the previous usage record are attributed to the next record (they
    always precede their own response's record); Codex exposes no reliable failure signal, so
    ``tool_failures`` stays None there.
    """
    calls: dict[str, dict[str, Any]] = {}
    rejected = 0
    cwd = model = session_id = None
    pending: list[str] = []                       # codex: tool names since the last usage record
    seen_codex_calls: set[str] = set()
    tools: dict[str, dict[str, str]] = {}         # claude: request id -> {tool_use id: label}
    owner: dict[str, str] = {}                    # claude: tool_use id -> request id
    errors: dict[str, bool] = {}                  # claude: tool_use id -> is_error
    bumps: dict[str, int] = {}                    # claude: request id -> latest line that touched it
    for seq, line in enumerate(lines):
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(event, dict):
            continue
        if agent == "codex":
            payload = event.get("payload")
            if not isinstance(payload, dict):
                continue
            kind = event.get("type")
            if kind == "session_meta":
                cwd, session_id = payload.get("cwd") or cwd, payload.get("id") or session_id
            elif kind == "turn_context":
                cwd, model = payload.get("cwd") or cwd, payload.get("model") or model
            elif kind == "response_item" and payload.get("type") in ("function_call", "custom_tool_call"):
                call_id = payload.get("call_id")
                if call_id is None or call_id not in seen_codex_calls:
                    seen_codex_calls.add(call_id)
                    pending.append(tool_label(payload.get("name"), payload.get("namespace")))
            elif kind == "token_usage_record" and isinstance(payload.get("usage"), dict):
                request_id = payload.get("response_id")
                used, pending = pending, []
                if not request_id or not in_scope(cwd):
                    continue
                try:
                    row = _call_event(
                        agent, namespace, payload.get("session_id") or session_id, request_id,
                        model, cwd, event.get("timestamp"), payload["usage"], True, seq)
                except InvalidUsage:
                    rejected += 1
                    continue
                row["tool_calls"], row["tool_calls_total"] = _tools_field(used), len(used)
                calls[request_id] = row
        else:
            message = event.get("message")
            if not isinstance(message, dict):
                continue
            content = message.get("content")
            if event.get("type") == "user" and isinstance(content, list):
                for block in content:
                    if isinstance(block, dict) and block.get("type") == "tool_result":
                        use_id = block.get("tool_use_id")
                        if use_id in owner:
                            errors[use_id] = bool(block.get("is_error"))
                            bumps[owner[use_id]] = seq
                continue
            if event.get("type") != "assistant":
                continue
            usage, request_id = message.get("usage"), message.get("id")
            if not isinstance(usage, dict) or not request_id or not in_scope(event.get("cwd")):
                continue
            if isinstance(content, list):
                for block in content:
                    if isinstance(block, dict) and block.get("type") == "tool_use" and block.get("id"):
                        tools.setdefault(request_id, {})[block["id"]] = tool_label(block.get("name"))
                        owner[block["id"]] = request_id
            bumps[request_id] = seq
            final = message.get("stop_reason") is not None
            previous = calls.get(request_id)
            if previous and previous["event_status"] == "final" and not final:
                continue  # never let a late partial replace a final revision
            try:
                calls[request_id] = _call_event(
                    agent, namespace, event.get("sessionId"), request_id, message.get("model"),
                    event.get("cwd"), event.get("timestamp"), usage, final, seq)
            except InvalidUsage:
                rejected += 1
    if agent != "codex":
        for request_id, row in calls.items():
            used = tools.get(request_id, {})
            row["tool_calls"], row["tool_calls_total"] = _tools_field(used.values()), len(used)
            row["tool_failures"] = sum(1 for use_id in used if errors.get(use_id))
            row["revision"] = max(row["revision"], bumps.get(request_id, 0))
    return list(calls.values()), rejected
