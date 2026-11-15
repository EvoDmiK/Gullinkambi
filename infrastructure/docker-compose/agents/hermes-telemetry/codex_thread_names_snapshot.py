#!/usr/bin/env python3
"""Write a minimal snapshot of Codex thread names for the telemetry container.

The Codex state database runs in WAL mode, and Codex deletes and recreates its
-wal/-shm files. Bind-mounting those files individually leaves the container
with missing files, so it cannot open the database. This LaunchAgent copies only
the columns the collector needs (threads.id, name, title) into a rollback-journal
SQLite file and atomically replaces it inside a directory that the container
mounts read-only.
"""

from __future__ import annotations

import argparse
import os
import sqlite3
import sys
import tempfile
from contextlib import closing
from pathlib import Path


def snapshot(source: Path, target: Path) -> int:
    with closing(sqlite3.connect(f"{source.as_uri()}?mode=ro", uri=True, timeout=5)) as src:
        rows = src.execute("SELECT id, name, title FROM threads").fetchall()

    target.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=".threads-", suffix=".sqlite", dir=target.parent)
    os.close(fd)
    try:
        with closing(sqlite3.connect(temp_name)) as dst:
            dst.execute("PRAGMA journal_mode=DELETE")
            dst.execute("CREATE TABLE threads (id TEXT PRIMARY KEY, name TEXT, title TEXT)")
            dst.executemany("INSERT INTO threads VALUES (?, ?, ?)", rows)
            dst.commit()
        os.chmod(temp_name, 0o644)
        os.replace(temp_name, target)
    except BaseException:
        Path(temp_name).unlink(missing_ok=True)
        raise
    return len(rows)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=Path.home() / ".codex/state_5.sqlite")
    parser.add_argument(
        "--target",
        type=Path,
        default=Path.home() / "Library/Application Support/Gullinkambi/codex-state/threads.sqlite",
    )
    args = parser.parse_args()
    try:
        snapshot(args.source, args.target)
    except (OSError, sqlite3.Error) as exc:
        print(f"codex thread snapshot failed: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
