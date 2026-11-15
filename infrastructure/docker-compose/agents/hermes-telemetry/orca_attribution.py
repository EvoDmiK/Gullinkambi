"""Manifest/result validation and explicit task/attempt attribution.

Inputs are local JSON documents with opaque IDs only. Titles, paths and free text
are rejected by an allowlist, so nothing sensitive can reach telemetry through them.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import re
from datetime import datetime, timezone
from typing import Any, Iterable

import orca_calls

ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}")
SHA_RE = re.compile(r"[0-9a-f]{64}")
HASH_RE = re.compile(r"[0-9a-f]{24}")  # orca_calls._hash()
STATUSES = ("completed", "failed", "cancelled", "blocked", "unknown")
VERIFICATION_STATES = ("unverified", "evidence_verified", "failed", "unknown")
MANIFEST_KEYS = {"schema_version", "producer_id", "task_id", "attempt_id", "project_key",
                 "session_id", "session_id_hash", "request_ids", "request_id_hashes", "window", "revision",
                 "parent_task_id", "role"}
RESULT_KEYS = {"schema_version", "event_id", "task_id", "attempt_id", "result_revision",
               "occurred_at", "producer_id", "producer_type", "evidence_kind",
               "reported_status", "verification_state", "test", "artifact_handle", "artifact_sha256", "signature"}
TEST_KEYS = {"test_id", "exit_code", "passed", "failed", "skipped"}


class InvalidInput(ValueError):
    pass


def _id(doc: dict, key: str, required=True) -> str | None:
    value = doc.get(key)
    if value is None and not required:
        return None
    if not isinstance(value, str) or not ID_RE.fullmatch(value):
        raise InvalidInput(f"{key}_invalid")
    return value


def _utc(value: Any, key: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        raise InvalidInput(f"{key}_invalid") from None
    if parsed.tzinfo is None:
        raise InvalidInput(f"{key}_not_utc")
    return parsed.astimezone(timezone.utc)


def _int(doc: dict, key: str, minimum=0) -> int:
    value = doc.get(key)
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise InvalidInput(f"{key}_invalid")
    return value


def _reject_extra(doc: dict, allowed: set[str]) -> None:
    extra = set(doc) - allowed
    if extra:
        raise InvalidInput("unknown_field:" + ",".join(sorted(extra)))


def validate_manifest(doc: Any) -> dict[str, Any]:
    if not isinstance(doc, dict):
        raise InvalidInput("not_an_object")
    _reject_extra(doc, MANIFEST_KEYS)
    if doc.get("schema_version") != 1:
        raise InvalidInput("schema_version_unsupported")
    window = doc.get("window")
    request_ids, request_hashes = doc.get("request_ids"), doc.get("request_id_hashes")
    if (request_ids is not None) and (request_hashes is not None):
        raise InvalidInput("request_ids_and_hashes_both_given")
    has_requests = request_ids is not None or request_hashes is not None
    if has_requests == (window is not None):
        raise InvalidInput("exactly_one_of_request_ids_or_window")
    session_raw, session_hash = doc.get("session_id"), doc.get("session_id_hash")
    if session_raw is not None and session_hash is not None:
        raise InvalidInput("session_id_and_hash_both_given")
    if window is not None and session_raw is None and session_hash is None:
        raise InvalidInput("session_id_invalid")
    out = {
        "producer_id": _id(doc, "producer_id"), "task_id": _id(doc, "task_id"),
        "attempt_id": _id(doc, "attempt_id"), "project_key": _id(doc, "project_key"),
        "parent_task_id": _id(doc, "parent_task_id", required=False),
        "role": _id(doc, "role", required=False), "revision": _int(doc, "revision", 1),
    }
    # Only hashes leave this module: raw session/request IDs are never stored or forwarded.
    if session_hash is not None:
        if not isinstance(session_hash, str) or not HASH_RE.fullmatch(session_hash):
            raise InvalidInput("session_id_hash_invalid")
        out["session_hash"] = session_hash
    elif session_raw is not None:
        out["session_hash"] = orca_calls._hash(_id(doc, "session_id"))
    else:
        out["session_hash"] = None
    if has_requests:
        values = request_hashes if request_hashes is not None else request_ids
        if not isinstance(values, list) or not values:
            raise InvalidInput("request_ids_invalid")
        if request_hashes is not None:
            if not all(isinstance(r, str) and HASH_RE.fullmatch(r) for r in values):
                raise InvalidInput("request_id_hashes_invalid")
            out["request_hashes"] = frozenset(values)
        else:
            if not all(isinstance(r, str) and r and len(r) <= 256 for r in values):
                raise InvalidInput("request_ids_invalid")
            out["request_hashes"] = frozenset(orca_calls._hash(r) for r in values)
    else:
        if not isinstance(window, dict) or set(window) != {"start", "end"}:
            raise InvalidInput("window_invalid")
        start, end = _utc(window["start"], "window.start"), _utc(window["end"], "window.end")
        if start >= end:
            raise InvalidInput("window_empty")
        out["window"] = (start, end)  # [start, end)
    return out


def validate_result(doc: Any) -> dict[str, Any]:
    if not isinstance(doc, dict):
        raise InvalidInput("not_an_object")
    _reject_extra(doc, RESULT_KEYS)
    if doc.get("schema_version") != 1:
        raise InvalidInput("schema_version_unsupported")
    out = {
        "event_id": _id(doc, "event_id"), "task_id": _id(doc, "task_id"),
        "attempt_id": _id(doc, "attempt_id"), "producer_id": _id(doc, "producer_id"),
        "result_revision": _int(doc, "result_revision", 1),
        "occurred_at": _utc(doc.get("occurred_at"), "occurred_at"),
    }
    for key, allowed in (("producer_type", ("agent", "verifier", "test_runner", "human")),
                         ("evidence_kind", ("real", "fixture")),
                         ("reported_status", STATUSES),
                         ("verification_state", VERIFICATION_STATES)):
        if doc.get(key) not in allowed:
            raise InvalidInput(f"{key}_invalid")
        out[key] = doc[key]
    if "test" in doc:
        test = doc["test"]
        if not isinstance(test, dict):
            raise InvalidInput("test_invalid")
        _reject_extra(test, TEST_KEYS)
        out["test"] = {"test_id": _id(test, "test_id"), "exit_code": _int(test, "exit_code", -255)}
        for key in ("passed", "failed", "skipped"):
            if key in test:
                out["test"][key] = _int(test, key)
    if "artifact_handle" in doc:
        out["artifact_handle"] = _id(doc, "artifact_handle")
        if not SHA_RE.fullmatch(str(doc.get("artifact_sha256", ""))):
            raise InvalidInput("artifact_sha256_invalid")
        out["artifact_sha256"] = doc["artifact_sha256"]
    signature = doc.get("signature")
    if signature is not None and not (isinstance(signature, str) and SHA_RE.fullmatch(signature)):
        raise InvalidInput("signature_invalid")
    out["signature"] = signature
    out["_signed_bytes"] = _canonical(doc)
    return out


def _canonical(doc: dict[str, Any]) -> bytes:
    """Stable bytes of a result document without its signature (what the verifier signs)."""
    body = {k: v for k, v in doc.items() if k != "signature"}
    return json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def sign_result(doc: dict[str, Any], key: str) -> str:
    return hmac.new(key.encode("utf-8"), _canonical(doc), hashlib.sha256).hexdigest()


def signature_valid(result: dict[str, Any], key: str) -> bool:
    signature = result.get("signature")
    if not key or not signature:
        return False
    expected = hmac.new(key.encode("utf-8"), result["_signed_bytes"], hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature)


def effective_verification(result: dict[str, Any], approved_verifiers: Iterable[str], verifier_key: str | None = None) -> str:
    """`evidence_verified` counts only from an approved verifier on real evidence.

    When `verifier_key` is configured the result must also carry a valid HMAC signature, so a
    producer_id that merely claims to be the verifier is not enough. The key is a shared secret
    between the verifier host and the pipeline; it does not protect against a party that can read
    the verifier's key file.
    """
    state = result["verification_state"]
    if state != "evidence_verified":
        return state
    trusted = (result["producer_type"] == "verifier"
               and result["producer_id"] in set(approved_verifiers)
               and result["evidence_kind"] == "real"
               and (not verifier_key or signature_valid(result, verifier_key)))
    return state if trusted else "unverified"


def latest_manifests(manifests: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """Highest revision per (task, attempt) wins; corrections supersede older claims."""
    best: dict[tuple[str, str], dict[str, Any]] = {}
    for m in manifests:
        key = (m["task_id"], m["attempt_id"])
        if key not in best or m["revision"] > best[key]["revision"]:
            best[key] = m
    return list(best.values())


def attribute(event: dict[str, Any], manifests: Iterable[dict[str, Any]], hash_fn=None) -> dict[str, Any]:
    """Assign one call to at most one leaf attempt; disagreement yields `ambiguous`."""
    claims: dict[tuple[str, str], str] = {}
    occurred = None
    if event.get("occurred_at"):
        occurred = _utc(event["occurred_at"], "occurred_at")
    for m in latest_manifests(manifests):
        method = None
        if "request_hashes" in m:
            if event["source_request_id_hash"] in m["request_hashes"]:
                method = "explicit_request"
        elif event["session_id_hash"] == m["session_hash"]:
            if occurred is None:
                claims[(m["task_id"], m["attempt_id"])] = "time_unknown"
                continue
            if m["window"][0] <= occurred < m["window"][1]:
                method = "session_window"
        if method:
            claims[(m["task_id"], m["attempt_id"])] = method
    real = {k: v for k, v in claims.items() if v != "time_unknown"}
    if len(claims) > 1 or (claims and not real):
        return {"task_id": None, "attempt_id": None, "attribution_method": None,
                "attribution_status": "ambiguous"}
    if not real:
        return {"task_id": None, "attempt_id": None, "attribution_method": None,
                "attribution_status": "unassigned"}
    (task_id, attempt_id), method = next(iter(real.items()))
    return {"task_id": task_id, "attempt_id": attempt_id, "attribution_method": method,
            "attribution_status": "explicit"}
