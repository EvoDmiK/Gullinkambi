#!/usr/bin/env python3
"""Expose Nginx Proxy Manager traffic and certificate expiry as Prometheus metrics."""

import json
import os
import re
import sqlite3
import sys
import threading
import time
from calendar import timegm
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


LOG_DIR = Path(os.environ.get("NPM_LOG_DIR", "/npm/logs"))
DB_PATH = Path(os.environ.get("NPM_DATABASE", "/npm/database.sqlite"))
HOST = os.environ.get("NPM_EXPORTER_HOST", "0.0.0.0")
PORT = int(os.environ.get("NPM_EXPORTER_PORT", "9823"))
INTERVAL = float(os.environ.get("NPM_SCAN_INTERVAL_SECONDS", "15"))

LOG_NAME = re.compile(r"^proxy-host-(\d+)_access\.log$")
# NPM "proxy" log_format:
# [$time_local] $upstream_cache_status $upstream_status $status - $request_method $scheme $host
# "$request_uri" [Client $remote_addr] [Length $body_bytes_sent] ...
LINE = re.compile(
    rb'^\[[^\]]*\] \S+ \S+ (?P<status>\d{3}) - \S+ \S+ \S+ "(?:[^"\\]|\\.)*" '
    rb"\[Client [^\]]*\] \[Length (?P<length>\d+)\]"
)


def metric(name, value, labels=None):
    label_text = ""
    if labels:
        parts = []
        for key, raw_value in sorted(labels.items()):
            escaped = str(raw_value).replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")
            parts.append(f'{key}="{escaped}"')
        label_text = "{" + ",".join(parts) + "}"
    return f"{name}{label_text} {value}"


def first_domain(raw):
    try:
        names = json.loads(raw)
    except (TypeError, ValueError):
        return None
    if isinstance(names, list) and names and isinstance(names[0], str):
        return names[0]
    return None


def parse_utc(value):
    try:
        return float(timegm(time.strptime(value, "%Y-%m-%d %H:%M:%S")))
    except (TypeError, ValueError):
        return None


def read_database():
    """Return active proxy host domains and expiry of certificates in use."""
    uri = f"file:{DB_PATH}?mode=ro"
    with sqlite3.connect(uri, uri=True, timeout=5) as conn:
        hosts = {}
        for host_id, domains in conn.execute(
            "SELECT id, domain_names FROM proxy_host WHERE is_deleted = 0"
        ):
            domain = first_domain(domains)
            if domain:
                hosts[int(host_id)] = domain

        certificates = {}
        for nice_name, domains, expires_on in conn.execute(
            """
            SELECT c.nice_name, c.domain_names, c.expires_on
            FROM certificate c
            WHERE c.is_deleted = 0
              AND c.id IN (
                SELECT certificate_id FROM proxy_host WHERE is_deleted = 0 AND enabled = 1
                UNION
                SELECT certificate_id FROM redirection_host WHERE is_deleted = 0 AND enabled = 1
              )
            """
        ):
            # Custom certificates can carry a parsed subject instead of a domain list,
            # so label by the name shown in NPM.
            domain = nice_name or first_domain(domains)
            expires = parse_utc(expires_on)
            if domain and expires is not None:
                certificates[domain] = min(expires, certificates.get(domain, expires))
    return hosts, certificates


class LogTail:
    """Follow one access log across NPM's rename-and-create logrotate."""

    def __init__(self, path, from_start):
        self.path = path
        self.inode = None
        self.offset = 0
        self.partial = b""
        if not from_start:
            try:
                stat = path.stat()
                self.inode = stat.st_ino
                self.offset = stat.st_size
            except OSError:
                pass

    def read_lines(self):
        try:
            stat = self.path.stat()
        except OSError:
            return []
        if stat.st_ino != self.inode or stat.st_size < self.offset:
            self.inode = stat.st_ino
            self.offset = 0
            self.partial = b""
        if stat.st_size == self.offset:
            return []
        with self.path.open("rb") as handle:
            handle.seek(self.offset)
            chunk = handle.read()
        self.offset += len(chunk)
        lines = (self.partial + chunk).split(b"\n")
        self.partial = lines.pop()
        return lines


class Collector:
    def __init__(self):
        self.lock = threading.Lock()
        self.tails = {}
        self.started = False
        self.requests = {}
        self.response_bytes = {}
        self.parse_errors = 0
        self.certificates = {}
        self.last_success = None
        self.up = 0

    def scan(self):
        hosts, certificates = read_database()
        requests = {}
        response_bytes = {}
        parse_errors = 0
        seen = set()
        for path in LOG_DIR.iterdir():
            match = LOG_NAME.match(path.name)
            if not match:
                continue
            host = hosts.get(int(match.group(1)))
            if host is None:
                continue
            seen.add(path.name)
            tail = self.tails.get(path.name)
            if tail is None:
                # Skip history on startup; read new hosts' logs from the beginning.
                tail = self.tails[path.name] = LogTail(path, from_start=self.started)
            for line in tail.read_lines():
                if not line:
                    continue
                parsed = LINE.match(line)
                if parsed is None:
                    parse_errors += 1
                    continue
                key = (host, parsed.group("status").decode())
                requests[key] = requests.get(key, 0) + 1
                response_bytes[host] = response_bytes.get(host, 0) + int(parsed.group("length"))
        for name in set(self.tails) - seen:
            del self.tails[name]
        self.started = True

        with self.lock:
            for key, count in requests.items():
                self.requests[key] = self.requests.get(key, 0) + count
            for host, size in response_bytes.items():
                self.response_bytes[host] = self.response_bytes.get(host, 0) + size
            self.parse_errors += parse_errors
            self.certificates = certificates
            self.last_success = time.time()
            self.up = 1

    def run(self):
        while True:
            try:
                self.scan()
            except Exception as exc:
                with self.lock:
                    self.up = 0
                print(f"npm scan failed: {exc}", file=sys.stderr)
            time.sleep(INTERVAL)

    def render(self):
        with self.lock:
            lines = [
                "# HELP npm_http_requests_total Requests served per proxy host and HTTP status.",
                "# TYPE npm_http_requests_total counter",
            ]
            for (host, status), count in sorted(self.requests.items()):
                lines.append(metric("npm_http_requests_total", count, {"host": host, "status": status}))
            lines += [
                "# HELP npm_http_response_bytes_total Response body bytes sent per proxy host.",
                "# TYPE npm_http_response_bytes_total counter",
            ]
            for host, size in sorted(self.response_bytes.items()):
                lines.append(metric("npm_http_response_bytes_total", size, {"host": host}))
            lines += [
                "# HELP npm_certificate_expiry_timestamp_seconds Expiry of certificates used by active hosts.",
                "# TYPE npm_certificate_expiry_timestamp_seconds gauge",
            ]
            for domain, expires in sorted(self.certificates.items()):
                lines.append(
                    metric("npm_certificate_expiry_timestamp_seconds", f"{expires:.0f}", {"domain": domain})
                )
            lines += [
                "# HELP npm_log_parse_errors_total Access log lines that did not match the NPM proxy format.",
                "# TYPE npm_log_parse_errors_total counter",
                metric("npm_log_parse_errors_total", self.parse_errors),
                "# HELP npm_collector_up Whether the latest scan succeeded.",
                "# TYPE npm_collector_up gauge",
                metric("npm_collector_up", self.up),
            ]
            if self.last_success is not None:
                lines += [
                    "# HELP npm_collector_last_success_timestamp_seconds Last successful scan time.",
                    "# TYPE npm_collector_last_success_timestamp_seconds gauge",
                    metric("npm_collector_last_success_timestamp_seconds", f"{self.last_success:.3f}"),
                ]
        return "\n".join(lines) + "\n"


COLLECTOR = Collector()


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/healthz":
            body = b"ok\n"
            content_type = "text/plain; charset=utf-8"
            status = 200
        elif self.path == "/metrics":
            body = COLLECTOR.render().encode()
            content_type = "text/plain; version=0.0.4; charset=utf-8"
            status = 200
        else:
            body = b"not found\n"
            content_type = "text/plain; charset=utf-8"
            status = 404
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format, *args):
        return


def main():
    threading.Thread(target=COLLECTOR.run, daemon=True).start()
    server = ThreadingHTTPServer((HOST, PORT), Handler)
    print(f"NPM exporter listening on http://{HOST}:{PORT}/metrics", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
