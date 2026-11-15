#!/usr/bin/env python3
import json
import os
import sqlite3
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


DATA_DIR = Path(os.environ.get("OPENVIKING_DATA_DIR", "/openviking/data"))
HOST = os.environ.get("OPENVIKING_EXPORTER_HOST", "0.0.0.0")
PORT = int(os.environ.get("OPENVIKING_EXPORTER_PORT", "9822"))


def metric(name, value, labels=None):
    label_text = ""
    if labels:
        parts = []
        for key, raw_value in sorted(labels.items()):
            escaped = str(raw_value).replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")
            parts.append(f'{key}="{escaped}"')
        label_text = "{" + ",".join(parts) + "}"
    return f"{name}{label_text} {value}"


def dir_size_bytes(path):
    total = 0
    if not path.exists():
        return 0
    for item in path.rglob("*"):
        try:
            if item.is_file():
                total += item.stat().st_size
        except OSError:
            continue
    return total


def file_line_count(path):
    try:
        with path.open("rb") as handle:
            return sum(1 for _ in handle)
    except OSError:
        return 0


def sqlite_rows(db_path, query, params=()):
    try:
        uri = f"file:{db_path}?mode=ro"
        with sqlite3.connect(uri, uri=True, timeout=2) as conn:
            conn.row_factory = sqlite3.Row
            return conn.execute(query, params).fetchall()
    except sqlite3.Error:
        return []


def scalar_sqlite(db_path, query, params=(), default=0):
    rows = sqlite_rows(db_path, query, params)
    if not rows:
        return default
    value = rows[0][0]
    return default if value is None else value


def collect_queue_metrics(lines):
    db_path = DATA_DIR / "_system" / "queue" / "queue.db"
    rows = sqlite_rows(
        db_path,
        """
        SELECT queue_name, status, COUNT(*) AS count
        FROM queue_messages
        GROUP BY queue_name, status
        """,
    )
    for row in rows:
        lines.append(
            metric(
                "openviking_queue_messages",
                row["count"],
                {"queue": row["queue_name"], "status": row["status"]},
            )
        )


def collect_usage_metrics(lines):
    db_path = DATA_DIR / "_system" / "usage_audit" / "usage_audit.sqlite3"
    token_rows = sqlite_rows(
        db_path,
        """
        SELECT source, token_type, provider, model_name, SUM(token_count) AS token_count
        FROM usage_token_hourly
        GROUP BY source, token_type, provider, model_name
        """,
    )
    for row in token_rows:
        lines.append(
            metric(
                "openviking_usage_tokens_total",
                row["token_count"] or 0,
                {
                    "source": row["source"],
                    "token_type": row["token_type"],
                    "provider": row["provider"],
                    "model": row["model_name"],
                },
            )
        )

    retrieval_rows = sqlite_rows(
        db_path,
        """
        SELECT operation, status, SUM(request_count) AS request_count, SUM(result_count) AS result_count
        FROM usage_retrieval_hourly
        GROUP BY operation, status
        """,
    )
    for row in retrieval_rows:
        labels = {"operation": row["operation"], "status": row["status"]}
        lines.append(metric("openviking_retrieval_requests_total", row["request_count"] or 0, labels))
        lines.append(metric("openviking_retrieval_results_total", row["result_count"] or 0, labels))

    write_rows = sqlite_rows(
        db_path,
        """
        SELECT operation, SUM(count) AS count
        FROM usage_context_write_bucket
        GROUP BY operation
        """,
    )
    for row in write_rows:
        lines.append(metric("openviking_context_writes_total", row["count"] or 0, {"operation": row["operation"]}))

    request_rows = sqlite_rows(
        db_path,
        """
        SELECT api_type, method, route, status_code, COUNT(*) AS count, AVG(duration_ms) AS duration_ms_avg
        FROM request_audit
        GROUP BY api_type, method, route, status_code
        """,
    )
    for row in request_rows:
        labels = {
            "api_type": row["api_type"],
            "method": row["method"],
            "route": row["route"],
            "status_code": row["status_code"],
        }
        lines.append(metric("openviking_requests_total", row["count"] or 0, labels))
        lines.append(metric("openviking_request_duration_ms_avg", row["duration_ms_avg"] or 0, labels))

    recent_errors = scalar_sqlite(
        db_path,
        """
        SELECT COUNT(*)
        FROM request_audit
        WHERE status_code >= 400
          AND created_at >= datetime('now', '-1 hour')
        """,
    )
    lines.append(metric("openviking_request_errors_last_hour", recent_errors))


def collect_file_metrics(lines):
    paths = {
        "data": DATA_DIR,
        "vectordb_context": DATA_DIR / "vectordb" / "context",
        "bot_sessions": DATA_DIR / "bot" / "sessions",
        "bot_logs": DATA_DIR / "bot" / "logs",
        "queue": DATA_DIR / "_system" / "queue",
        "usage_audit": DATA_DIR / "_system" / "usage_audit",
        "viking": DATA_DIR / "viking",
    }
    for label, path in paths.items():
        lines.append(metric("openviking_path_bytes", dir_size_bytes(path), {"path": label}))

    session_dir = DATA_DIR / "bot" / "sessions"
    session_files = list(session_dir.glob("*.jsonl")) if session_dir.exists() else []
    lines.append(metric("openviking_session_files", len(session_files)))
    lines.append(metric("openviking_session_lines", sum(file_line_count(path) for path in session_files)))

    log_path = DATA_DIR / "bot" / "logs" / "vikingbot.log"
    lines.append(metric("openviking_log_lines", file_line_count(log_path), {"log": "vikingbot"}))

    meta_path = DATA_DIR / "vectordb" / "context" / "collection_meta.json"
    try:
        meta = json.loads(meta_path.read_text())
        for key, value in meta.items():
            if isinstance(value, bool):
                value = 1 if value else 0
            if isinstance(value, (int, float)):
                lines.append(metric("openviking_vectordb_collection_meta", value, {"field": key}))
    except (OSError, json.JSONDecodeError):
        pass


def collect_metrics():
    lines = [
        "# HELP openviking_exporter_up Whether the exporter can read the OpenViking data directory.",
        "# TYPE openviking_exporter_up gauge",
        metric("openviking_exporter_up", 1 if DATA_DIR.exists() else 0),
        "# TYPE openviking_path_bytes gauge",
        "# TYPE openviking_session_files gauge",
        "# TYPE openviking_session_lines gauge",
        "# TYPE openviking_log_lines gauge",
        "# TYPE openviking_queue_messages gauge",
        "# TYPE openviking_usage_tokens_total counter",
        "# TYPE openviking_retrieval_requests_total counter",
        "# TYPE openviking_retrieval_results_total counter",
        "# TYPE openviking_context_writes_total counter",
        "# TYPE openviking_requests_total counter",
        "# TYPE openviking_request_duration_ms_avg gauge",
        "# TYPE openviking_request_errors_last_hour gauge",
    ]
    collect_file_metrics(lines)
    collect_queue_metrics(lines)
    collect_usage_metrics(lines)
    lines.append(metric("openviking_exporter_scrape_timestamp_seconds", int(time.time())))
    return "\n".join(lines) + "\n"


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/health":
            self.send_response(200 if DATA_DIR.exists() else 503)
            self.end_headers()
            self.wfile.write(b"ok\n" if DATA_DIR.exists() else b"missing data dir\n")
            return
        if self.path != "/metrics":
            self.send_response(404)
            self.end_headers()
            return

        body = collect_metrics().encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; version=0.0.4; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt, *args):
        return


if __name__ == "__main__":
    server = ThreadingHTTPServer((HOST, PORT), Handler)
    server.serve_forever()
