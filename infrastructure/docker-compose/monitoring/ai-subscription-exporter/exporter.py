#!/usr/bin/env python3
"""Expose Codex, Claude, and Antigravity subscription usage as Prometheus metrics."""

from __future__ import annotations

import json
import math
import os
import select
import shlex
import subprocess
import sys
import threading
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any


def _number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)) and math.isfinite(float(value)):
        return float(value)
    return None


def _first_number(value: dict[str, Any], *keys: str) -> float | None:
    for key in keys:
        number = _number(value.get(key))
        if number is not None:
            return number
    return None


def _epoch_seconds(value: float | None) -> float | None:
    if value is None:
        return None
    return value / 1000.0 if value > 10_000_000_000 else value


@dataclass
class ProviderResult:
    provider: str
    labels: dict[str, str] = field(default_factory=dict)
    up: bool = False
    collected_at: float = field(default_factory=time.time)
    quotas: dict[str, tuple[float, float | None]] = field(default_factory=dict)
    tokens: dict[tuple[str, str], float] = field(default_factory=dict)
    sessions: float | None = None
    error: str = ""

    @property
    def identity(self) -> tuple[str, tuple[tuple[str, str], ...]]:
        return self.provider, tuple(sorted(self.labels.items()))


def parse_codex_rate_limits(payload: dict[str, Any]) -> dict[str, tuple[float, float | None]]:
    result = payload.get("result", payload)
    if not isinstance(result, dict):
        return {}
    limits = result.get("rateLimits", result.get("rate_limits", result))
    if not isinstance(limits, dict):
        return {}

    parsed: dict[str, tuple[float, float | None]] = {}
    aliases = (("primary", "session"), ("secondary", "weekly"))
    for source, fallback_window in aliases:
        item = limits.get(source)
        if not isinstance(item, dict):
            continue
        used = _first_number(item, "usedPercent", "used_percent", "usedPercentage")
        duration = _first_number(
            item, "windowDurationMins", "window_duration_mins"
        )
        reset = _epoch_seconds(
            _first_number(item, "resetsAt", "resets_at", "resetAt", "reset_at")
        )
        if used is not None:
            window = fallback_window if duration is None else (
                "session" if duration <= 360 else "weekly"
            )
            parsed[window] = (max(0.0, min(100.0, used)), reset)

    if parsed:
        return parsed

    items = limits.get("windows")
    if isinstance(items, list):
        for item in items:
            if not isinstance(item, dict):
                continue
            duration = _first_number(item, "windowDurationMins", "window_duration_mins")
            used = _first_number(item, "usedPercent", "used_percent", "usedPercentage")
            reset = _epoch_seconds(_first_number(item, "resetsAt", "resets_at"))
            if duration is None or used is None:
                continue
            window = "session" if duration <= 360 else "weekly"
            parsed[window] = (max(0.0, min(100.0, used)), reset)
    return parsed


TOKEN_KEYS = {
    "totalTokens": "total",
    "total_tokens": "total",
    "inputTokens": "input",
    "input_tokens": "input",
    "outputTokens": "output",
    "output_tokens": "output",
    "cachedInputTokens": "cached_input",
    "cached_input_tokens": "cached_input",
}


def parse_codex_tokens(payload: dict[str, Any]) -> dict[tuple[str, str], float]:
    best: dict[str, float] = {}

    def walk(value: Any) -> None:
        if isinstance(value, dict):
            for key, child in value.items():
                token_type = TOKEN_KEYS.get(key)
                number = _number(child) if token_type else None
                if token_type and number is not None:
                    best[token_type] = max(best.get(token_type, 0.0), number)
                elif isinstance(child, (dict, list)):
                    walk(child)
        elif isinstance(value, list):
            for child in value:
                walk(child)

    walk(payload.get("result", payload))
    return {(token_type, "all"): value for token_type, value in best.items()}


class CodexRpc:
    def __init__(self, command: list[str], timeout: float) -> None:
        self.command = command
        self.timeout = timeout
        self.process: subprocess.Popen[str] | None = None
        self.next_id = 1

    def __enter__(self) -> "CodexRpc":
        self.process = subprocess.Popen(
            self.command,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
        )
        self.request(
            "initialize",
            {"clientInfo": {"name": "ai-subscription-exporter", "version": "1.0.0"}},
        )
        self.notify("initialized", {})
        return self

    def __exit__(self, *_: object) -> None:
        if self.process and self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                self.process.kill()

    def _send(self, message: dict[str, Any]) -> None:
        if not self.process or not self.process.stdin:
            raise RuntimeError("Codex app-server is not running")
        self.process.stdin.write(json.dumps(message, separators=(",", ":")) + "\n")
        self.process.stdin.flush()

    def notify(self, method: str, params: dict[str, Any]) -> None:
        self._send({"jsonrpc": "2.0", "method": method, "params": params})

    def request(self, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        request_id = self.next_id
        self.next_id += 1
        message: dict[str, Any] = {"jsonrpc": "2.0", "id": request_id, "method": method}
        if params is not None:
            message["params"] = params
        self._send(message)
        if not self.process or not self.process.stdout:
            raise RuntimeError("Codex app-server stdout is unavailable")
        deadline = time.monotonic() + self.timeout
        while time.monotonic() < deadline:
            remaining = max(0.0, deadline - time.monotonic())
            ready, _, _ = select.select([self.process.stdout], [], [], remaining)
            if not ready:
                break
            line = self.process.stdout.readline()
            if not line:
                break
            response = json.loads(line)
            if response.get("id") != request_id:
                continue
            if "error" in response:
                raise RuntimeError(f"{method}: {response['error']}")
            return response
        stderr = ""
        if self.process.poll() is not None and self.process.stderr:
            stderr = self.process.stderr.read().strip()
        raise TimeoutError(f"{method} timed out" + (f": {stderr}" if stderr else ""))


def collect_codex() -> ProviderResult:
    result = ProviderResult(provider="codex")
    command = shlex.split(os.environ.get("CODEX_BIN", "codex"))
    timeout = float(os.environ.get("CODEX_TIMEOUT_SECONDS", "15"))
    try:
        with CodexRpc(command + ["app-server", "--listen", "stdio://"], timeout) as rpc:
            rate_payload = rpc.request("account/rateLimits/read", {})
            result.quotas = parse_codex_rate_limits(rate_payload)
            if not result.quotas:
                raise ValueError("Codex returned no subscription rate limits")
            try:
                usage_payload = rpc.request("account/usage/read", {})
                result.tokens = parse_codex_tokens(usage_payload)
            except Exception as exc:
                print(f"Codex token usage unavailable: {exc}", file=sys.stderr)
        result.up = True
    except Exception as exc:
        result.error = str(exc)
    return result


def parse_claude_status(payload: dict[str, Any]) -> dict[str, tuple[float, float | None]]:
    limits = payload.get("rate_limits")
    if not isinstance(limits, dict):
        return {}
    parsed: dict[str, tuple[float, float | None]] = {}
    for source, window in (("five_hour", "session"), ("seven_day", "weekly")):
        item = limits.get(source)
        if not isinstance(item, dict):
            continue
        used = _first_number(item, "used_percentage", "usedPercent")
        reset = _epoch_seconds(_first_number(item, "resets_at", "resetsAt"))
        if used is not None:
            parsed[window] = (max(0.0, min(100.0, used)), reset)
    return parsed


def _claude_access_token() -> str:
    """Read Claude Code's access token without refreshing it (a refresh would rotate the login)."""
    raw = ""
    try:
        raw = subprocess.run(
            ["security", "find-generic-password", "-s", "Claude Code-credentials", "-w"],
            capture_output=True, text=True, timeout=5, check=True,
        ).stdout
    except (OSError, subprocess.SubprocessError):
        path = Path("~/.claude/.credentials.json").expanduser()
        raw = path.read_text(encoding="utf-8") if path.exists() else ""
    credentials = json.loads(raw) if raw.strip() else {}
    oauth = credentials.get("claudeAiOauth", credentials)
    token = oauth.get("accessToken")
    if not isinstance(token, str) or not token:
        raise ValueError("Claude Code OAuth token not found")
    expires_at = _epoch_seconds(_number(oauth.get("expiresAt")))
    if expires_at is not None and expires_at <= time.time():
        raise ValueError("Claude Code OAuth token expired; use Claude Code to refresh it")
    return token


def parse_claude_usage(payload: dict[str, Any]) -> dict[str, tuple[float, float | None]]:
    parsed: dict[str, tuple[float, float | None]] = {}
    for source, window in (("five_hour", "session"), ("seven_day", "weekly")):
        item = payload.get(source)
        if not isinstance(item, dict):
            continue
        used = _first_number(item, "utilization")
        if used is not None:
            parsed[window] = (max(0.0, min(100.0, used)), _iso_timestamp(item.get("resets_at")))
    return parsed


_claude_usage_cache: tuple[float, dict[str, tuple[float, float | None]]] | None = None


def fetch_claude_usage() -> tuple[float, dict[str, tuple[float, float | None]]]:
    """Account-wide 5-hour/weekly usage, so usage from any device or client is included."""
    global _claude_usage_cache
    interval = float(os.environ.get("CLAUDE_USAGE_INTERVAL_SECONDS", "300"))
    cached = _claude_usage_cache
    if cached is not None and time.time() - cached[0] < interval:
        return cached
    request = urllib.request.Request(
        os.environ.get("CLAUDE_USAGE_URL", "https://api.anthropic.com/api/oauth/usage"),
        headers={
            "Authorization": f"Bearer {_claude_access_token()}",
            "anthropic-beta": "oauth-2025-04-20",
        },
    )
    with urllib.request.urlopen(request, timeout=float(os.environ.get("CLAUDE_USAGE_TIMEOUT_SECONDS", "10"))) as response:
        quotas = parse_claude_usage(json.load(response))
    if not quotas:
        raise ValueError("Claude usage response has no subscription limits")
    _claude_usage_cache = (time.time(), quotas)
    return _claude_usage_cache


def collect_claude() -> ProviderResult:
    result = ProviderResult(provider="claude")
    if os.environ.get("CLAUDE_USAGE_API", "1") != "0":
        try:
            result.collected_at, result.quotas = fetch_claude_usage()
            result.up = True
            return result
        except Exception as exc:  # fall back to the status-line cache below
            # str(exc) never contains the token: it is only sent in the request header.
            print(f"claude usage API unavailable, using status line cache: {type(exc).__name__}: {exc}", file=sys.stderr)
    path = Path(
        os.environ.get("CLAUDE_STATUS_FILE", "~/.cache/gullinkambi/claude-status.json")
    ).expanduser()
    max_age = float(os.environ.get("CLAUDE_MAX_AGE_SECONDS", "86400"))
    try:
        with path.open(encoding="utf-8") as handle:
            payload = json.load(handle)
        captured_at = _number(payload.get("captured_at")) or path.stat().st_mtime
        if time.time() - captured_at > max_age:
            raise ValueError(f"Claude status cache is older than {max_age:.0f}s")
        result.quotas = parse_claude_status(payload)
        if not result.quotas:
            raise ValueError("Claude status cache has no subscription rate limits")
        result.collected_at = captured_at
        result.up = True
    except Exception as exc:
        result.error = str(exc)
    return result


def _iso_timestamp(value: Any) -> float | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def parse_antigravity_usage(
    payload: dict[str, Any],
) -> dict[str, dict[str, tuple[float, float | None]]]:
    command = payload.get("command")
    if not isinstance(command, dict):
        return {}
    data = command.get("data")
    if not isinstance(data, dict):
        return {}
    groups = data.get("groups")
    if not isinstance(groups, list):
        return {}

    parsed: dict[str, dict[str, tuple[float, float | None]]] = {}
    for group in groups:
        if not isinstance(group, dict):
            continue
        buckets = group.get("buckets")
        if not isinstance(buckets, list):
            continue
        for bucket in buckets:
            if not isinstance(bucket, dict):
                continue
            bucket_id = bucket.get("id")
            if not isinstance(bucket_id, str):
                continue
            if bucket_id.startswith("gemini-"):
                pool = "gemini"
            elif bucket_id.startswith("3p-"):
                pool = "third_party"
            else:
                continue

            source_window = bucket.get("window")
            if source_window in ("5h", "five_hour", "session"):
                window = "session"
            elif source_window in ("7d", "weekly"):
                window = "weekly"
            else:
                continue
            remaining = _number(bucket.get("remaining_fraction"))
            if remaining is None:
                continue
            used = (1.0 - max(0.0, min(1.0, remaining))) * 100.0
            parsed.setdefault(pool, {})[window] = (
                used,
                _iso_timestamp(bucket.get("reset_time")),
            )
    return parsed


ANTIGRAVITY_POOLS = ("gemini", "third_party")


def collect_antigravity() -> list[ProviderResult]:
    command = shlex.split(os.environ.get("ANTIGRAVITY_BIN", "agy"))
    timeout = float(os.environ.get("ANTIGRAVITY_TIMEOUT_SECONDS", "45"))
    try:
        completed = subprocess.run(
            command + ["-p", "/usage", "--output-format", "json"],
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
        if completed.returncode != 0:
            detail = completed.stderr.strip()[-500:]
            raise RuntimeError(
                f"Antigravity usage command exited {completed.returncode}"
                + (f": {detail}" if detail else "")
            )
        payload = json.loads(completed.stdout)
        if not isinstance(payload, dict):
            raise ValueError("Antigravity returned a non-object usage payload")
        quotas_by_pool = parse_antigravity_usage(payload)
        if not quotas_by_pool:
            raise ValueError("Antigravity returned no model quota buckets")
        return [
            ProviderResult(
                provider="antigravity",
                labels={"pool": pool},
                up=pool in quotas_by_pool,
                quotas=quotas_by_pool.get(pool, {}),
                error=(
                    ""
                    if pool in quotas_by_pool
                    else f"Antigravity returned no quota bucket for {pool}"
                ),
            )
            for pool in ANTIGRAVITY_POOLS
        ]
    except Exception as exc:
        return [
            ProviderResult(
                provider="antigravity",
                labels={"pool": pool},
                error=str(exc),
            )
            for pool in ANTIGRAVITY_POOLS
        ]


def _label(value: str) -> str:
    return value.replace("\\", "\\\\").replace("\n", "\\n").replace('"', '\\"')


def _sample(name: str, value: float, labels: dict[str, str]) -> str:
    encoded = ",".join(f'{key}="{_label(item)}"' for key, item in sorted(labels.items()))
    return f"{name}{{{encoded}}} {value:.12g}"


class Collector:
    def __init__(self) -> None:
        self.cache_seconds = float(os.environ.get("AI_SUBSCRIPTION_CACHE_SECONDS", "60"))
        self.lock = threading.Lock()
        self.cached_at = 0.0
        self.results: list[ProviderResult] = []
        self.last_success: dict[tuple[str, tuple[tuple[str, str], ...]], float] = {}

    def collect(self) -> list[ProviderResult]:
        with self.lock:
            now = time.time()
            if self.results and now - self.cached_at < self.cache_seconds:
                return self.results
            with ThreadPoolExecutor(max_workers=3) as executor:
                codex_future = executor.submit(collect_codex)
                claude_future = executor.submit(collect_claude)
                antigravity_future = executor.submit(collect_antigravity)
                results = [
                    codex_future.result(),
                    claude_future.result(),
                    *antigravity_future.result(),
                ]
            for result in results:
                if result.up:
                    self.last_success[result.identity] = result.collected_at
                elif result.error:
                    identity = ",".join(
                        [result.provider, *(f"{key}={value}" for key, value in result.labels.items())]
                    )
                    print(f"{identity} collection failed: {result.error}", file=sys.stderr)
            self.results = results
            self.cached_at = now
            return results

    def render(self) -> str:
        results = self.collect()
        lines = [
            "# HELP ai_subscription_quota_used_percent Subscription quota consumed.",
            "# TYPE ai_subscription_quota_used_percent gauge",
            "# HELP ai_subscription_quota_reset_timestamp_seconds Subscription quota reset time.",
            "# TYPE ai_subscription_quota_reset_timestamp_seconds gauge",
            "# HELP ai_subscription_tokens_total Token usage reported by the provider.",
            "# TYPE ai_subscription_tokens_total counter",
            "# HELP ai_subscription_sessions_total Sessions reported by the provider.",
            "# TYPE ai_subscription_sessions_total counter",
            "# HELP ai_subscription_collector_up Whether the latest provider collection succeeded.",
            "# TYPE ai_subscription_collector_up gauge",
            "# HELP ai_subscription_last_success_timestamp_seconds Last successful collection time.",
            "# TYPE ai_subscription_last_success_timestamp_seconds gauge",
        ]
        for result in results:
            provider_labels = {"provider": result.provider, **result.labels}
            lines.append(
                _sample("ai_subscription_collector_up", 1.0 if result.up else 0.0, provider_labels)
            )
            success = self.last_success.get(result.identity)
            if success is not None:
                lines.append(
                    _sample("ai_subscription_last_success_timestamp_seconds", success, provider_labels)
                )
            for window, (used, reset) in sorted(result.quotas.items()):
                labels = {**provider_labels, "window": window}
                lines.append(_sample("ai_subscription_quota_used_percent", used, labels))
                if reset is not None:
                    lines.append(
                        _sample("ai_subscription_quota_reset_timestamp_seconds", reset, labels)
                    )
            for (token_type, model), value in sorted(result.tokens.items()):
                lines.append(
                    _sample(
                        "ai_subscription_tokens_total",
                        value,
                        {**provider_labels, "type": token_type, "model": model},
                    )
                )
            if result.sessions is not None:
                lines.append(
                    _sample("ai_subscription_sessions_total", result.sessions, provider_labels)
                )
        return "\n".join(lines) + "\n"


COLLECTOR = Collector()


class Handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802
        if self.path == "/healthz":
            body = b"ok\n"
            status = 200
            content_type = "text/plain; charset=utf-8"
        elif self.path == "/metrics":
            body = COLLECTOR.render().encode()
            status = 200
            content_type = "text/plain; version=0.0.4; charset=utf-8"
        else:
            body = b"not found\n"
            status = 404
            content_type = "text/plain; charset=utf-8"
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: object) -> None:
        print(f"{self.client_address[0]} - {format % args}", file=sys.stderr)


def main() -> int:
    host = os.environ.get("AI_SUBSCRIPTION_EXPORTER_HOST", "0.0.0.0")
    port = int(os.environ.get("AI_SUBSCRIPTION_EXPORTER_PORT", "9819"))
    server = ThreadingHTTPServer((host, port), Handler)
    print(f"AI subscription exporter listening on http://{host}:{port}/metrics")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
