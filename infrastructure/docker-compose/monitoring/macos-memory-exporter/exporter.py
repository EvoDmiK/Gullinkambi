#!/usr/bin/env python3
"""Small Prometheus exporter for macOS VM and memory-pressure metrics."""

from __future__ import annotations

import argparse
import re
import subprocess
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


VM_KEY_MAP = {
    "Pages free": "free",
    "Pages active": "active",
    "Pages inactive": "inactive",
    "Pages speculative": "speculative",
    "Pages wired down": "wired",
    "Pages purgeable": "purgeable",
    "File-backed pages": "file_backed",
    "Anonymous pages": "anonymous",
    "Pages stored in compressor": "compressor_stored",
    "Pages occupied by compressor": "compressed",
}


def run(command: list[str]) -> str:
    return subprocess.run(
        command,
        check=True,
        capture_output=True,
        text=True,
        timeout=5,
    ).stdout


def parse_vm_stat(text: str) -> tuple[int, dict[str, int]]:
    page_size_match = re.search(r"page size of (\d+) bytes", text)
    if not page_size_match:
        raise ValueError("vm_stat page size was not found")

    page_size = int(page_size_match.group(1))
    pages: dict[str, int] = {}
    for line in text.splitlines():
        if ":" not in line:
            continue
        raw_key, raw_value = line.split(":", 1)
        key = VM_KEY_MAP.get(raw_key.strip().strip('"'))
        if key is None:
            continue
        value_match = re.search(r"(\d+)", raw_value.replace(".", ""))
        if value_match:
            pages[key] = int(value_match.group(1))

    missing = set(VM_KEY_MAP.values()) - pages.keys()
    if missing:
        raise ValueError(f"vm_stat fields were not found: {sorted(missing)}")
    return page_size, pages


def parse_byte_size(value: str, unit: str) -> int:
    factors = {"K": 1024, "M": 1024**2, "G": 1024**3, "T": 1024**4}
    return int(float(value) * factors[unit.upper()])


def parse_swapusage(text: str) -> tuple[int, int]:
    match = re.search(
        r"total\s*=\s*([\d.]+)([KMGT])\s+used\s*=\s*([\d.]+)([KMGT])",
        text,
        re.IGNORECASE,
    )
    if not match:
        raise ValueError("vm.swapusage values were not found")
    total = parse_byte_size(match.group(1), match.group(2))
    used = parse_byte_size(match.group(3), match.group(4))
    return total, used


def collect() -> dict[str, float | int | dict[str, int]]:
    total = int(run(["/usr/sbin/sysctl", "-n", "hw.memsize"]).strip())
    page_size, pages = parse_vm_stat(run(["/usr/bin/vm_stat"]))
    pressure_ratio = int(run(["/usr/sbin/sysctl", "-n", "kern.memorystatus_level"]).strip()) / 100.0
    pressure_state = int(
        run(["/usr/sbin/sysctl", "-n", "kern.memorystatus_vm_pressure_level"]).strip()
    )
    swap_total, swap_used = parse_swapusage(run(["/usr/sbin/sysctl", "-n", "vm.swapusage"]))

    values = {name: count * page_size for name, count in pages.items()}
    used = max(
        values["active"]
        + values["inactive"]
        + values["speculative"]
        + values["wired"]
        + values["compressed"]
        - values["purgeable"]
        - values["file_backed"],
        0,
    )

    # This mirrors macmon's used-memory formula and turns it into a disjoint,
    # total-preserving dashboard breakdown. "cache" is an approximation based
    # on file-backed and purgeable pages; "free_other" is the residual.
    application = max(used - values["wired"] - values["compressed"], 0)
    unoccupied = max(total - used, 0)
    cache = min(values["file_backed"] + values["purgeable"], unoccupied)
    free_other = max(unoccupied - cache, 0)

    return {
        "total": total,
        "used": used,
        "pressure_available_ratio": pressure_ratio,
        "pressure_state": pressure_state,
        "swap_total": swap_total,
        "swap_used": swap_used,
        "pages": values,
        "breakdown": {
            "application": application,
            "wired": values["wired"],
            "compressed": values["compressed"],
            "cache_reclaimable": cache,
            "free_other": free_other,
        },
    }


def metric(name: str, help_text: str, value: float | int, labels: str = "") -> list[str]:
    return [
        f"# HELP {name} {help_text}",
        f"# TYPE {name} gauge",
        f"{name}{labels} {value}",
    ]


def metric_family(
    name: str,
    help_text: str,
    samples: dict[str, int],
    label_name: str = "type",
) -> list[str]:
    lines = [f"# HELP {name} {help_text}", f"# TYPE {name} gauge"]
    lines.extend(f'{name}{{{label_name}="{label}"}} {value}' for label, value in samples.items())
    return lines


def render(metrics: dict[str, float | int | dict[str, int]]) -> str:
    lines: list[str] = []
    lines += metric("macos_memory_total_bytes", "Total physical memory in bytes.", metrics["total"])
    lines += metric(
        "macos_memory_used_bytes",
        "Used physical memory in bytes using the macmon-compatible formula.",
        metrics["used"],
    )
    lines += metric(
        "macos_memory_pressure_available_ratio",
        "Kernel-estimated available-memory ratio; overlaps used memory.",
        metrics["pressure_available_ratio"],
    )
    lines += metric(
        "macos_memory_pressure_state",
        "Discrete macOS memory-pressure state: 1 normal, 2 warning, 4 critical.",
        metrics["pressure_state"],
    )
    lines += metric("macos_swap_total_bytes", "Configured swap capacity in bytes.", metrics["swap_total"])
    lines += metric("macos_swap_used_bytes", "Used swap in bytes.", metrics["swap_used"])

    pages = metrics["pages"]
    assert isinstance(pages, dict)
    lines += metric_family(
        "macos_memory_vm_bytes",
        "macOS VM page counters converted to physical bytes.",
        dict(sorted(pages.items())),
    )

    breakdown = metrics["breakdown"]
    assert isinstance(breakdown, dict)
    lines += metric_family(
        "macos_memory_breakdown_bytes",
        "Disjoint macmon-compatible memory breakdown that sums to total physical memory.",
        breakdown,
    )
    lines += metric("macos_memory_exporter_scrape_success", "Whether the latest collection succeeded.", 1)
    return "\n".join(lines) + "\n"


class Handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802
        if self.path not in ("/", "/metrics"):
            self.send_error(404)
            return
        try:
            body = render(collect()).encode()
            status = 200
        except Exception as exc:  # exporter must surface failures to Prometheus
            body = (
                "# HELP macos_memory_exporter_scrape_success Whether the latest collection succeeded.\n"
                "# TYPE macos_memory_exporter_scrape_success gauge\n"
                "macos_memory_exporter_scrape_success 0\n"
                f"# collection_error {type(exc).__name__}: {exc}\n"
            ).encode()
            status = 500
        self.send_response(status)
        self.send_header("Content-Type", "text/plain; version=0.0.4; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: object) -> None:
        return


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=9092)
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()

    if args.once:
        print(render(collect()), end="")
        return
    ThreadingHTTPServer((args.host, args.port), Handler).serve_forever()


if __name__ == "__main__":
    main()
