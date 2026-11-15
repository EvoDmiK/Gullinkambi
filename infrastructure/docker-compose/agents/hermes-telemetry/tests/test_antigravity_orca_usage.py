from __future__ import annotations

import importlib.util
import json
import sqlite3
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path


MODULE_PATH = Path(__file__).parents[1] / "antigravity_orca_usage.py"
SPEC = importlib.util.spec_from_file_location("antigravity_orca_usage", MODULE_PATH)
assert SPEC and SPEC.loader
usage = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = usage
SPEC.loader.exec_module(usage)


def varint(value: int) -> bytes:
    encoded = bytearray()
    while True:
        byte = value & 0x7F
        value >>= 7
        encoded.append(byte | (0x80 if value else 0))
        if not value:
            return bytes(encoded)


def protobuf_varint(field: int, value: int) -> bytes:
    return varint(field << 3) + varint(value)


def protobuf_bytes(field: int, value: bytes) -> bytes:
    return varint((field << 3) | 2) + varint(len(value)) + value


def create_conversation(path: Path, timestamp: int) -> None:
    connection = sqlite3.connect(path)
    connection.executescript(
        """
        CREATE TABLE gen_metadata (idx INTEGER PRIMARY KEY, data BLOB, size INTEGER);
        CREATE TABLE steps (idx INTEGER PRIMARY KEY, metadata BLOB);
        """
    )
    connection.execute(
        "INSERT INTO gen_metadata VALUES (0, ?, ?)",
        (b"prefix gemini-3.8-flash-high suffix", 35),
    )
    timestamp_message = protobuf_varint(1, timestamp) + protobuf_varint(2, 0)
    usage_message = b"".join(
        (
            protobuf_varint(2, 2589),
            protobuf_varint(3, 88),
            protobuf_varint(5, 16295),
            protobuf_varint(9, 87),
        )
    )
    metadata = protobuf_bytes(1, timestamp_message) + protobuf_bytes(9, usage_message)
    connection.execute("INSERT INTO steps VALUES (1, ?)", (metadata,))
    connection.commit()
    connection.close()


class AntigravityOrcaUsageTests(unittest.TestCase):
    def test_builds_privacy_safe_daily_aggregate_for_orca_workspace(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            antigravity_dir = root / "antigravity-cli"
            conversations = antigravity_dir / "conversations"
            conversations.mkdir(parents=True)
            orca_root = root / "orca"
            workspace = orca_root / "workspaces" / "Birds-Nest" / "dev-mac"
            workspace.mkdir(parents=True)

            timestamp = int(
                datetime(2026, 10, 1, 8, 17, tzinfo=timezone.utc).timestamp()
            )
            conversation_id = "2d6f89b4-efc3-42da-9fb5-cdb54155fbe9"
            create_conversation(conversations / f"{conversation_id}.db", timestamp)

            summaries = sqlite3.connect(antigravity_dir / "conversation_summaries.db")
            summaries.execute(
                """
                CREATE TABLE conversation_summaries (
                    conversation_id TEXT PRIMARY KEY,
                    workspace_uris TEXT NOT NULL,
                    title TEXT
                )
                """
            )
            summaries.execute(
                "INSERT INTO conversation_summaries VALUES (?, ?, ?)",
                (conversation_id, json.dumps([workspace.as_uri()]), "  Fix   telemetry\nlabels "),
            )
            summaries.execute(
                "INSERT INTO conversation_summaries VALUES (?, ?, ?)",
                (
                    "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
                    json.dumps([(root / "outside").as_uri()]),
                    "outside",
                ),
            )
            summaries.commit()
            summaries.close()

            document = usage.build_document(antigravity_dir, (orca_root,))
            self.assertEqual(len(document["dailyAggregates"]), 1)
            aggregate = document["dailyAggregates"][0]
            self.assertEqual(aggregate["day"], "2026-10-01")
            self.assertEqual(aggregate["model"], "gemini-3.8-flash-high")
            self.assertEqual(aggregate["projectLabel"], "Birds-Nest")
            self.assertEqual(aggregate["requestCount"], 1)
            self.assertEqual(aggregate["inputTokens"], 2589)
            self.assertEqual(aggregate["cacheReadTokens"], 16295)
            self.assertEqual(aggregate["outputTokens"], 88)
            self.assertEqual(aggregate["reasoningTokens"], 87)
            self.assertEqual(aggregate["totalTokens"], 2677)
            self.assertEqual(len(document["threadAggregates"]), 1)
            thread = document["threadAggregates"][0]
            self.assertEqual(thread["threadId"], usage.safe_hash(f"antigravity:{conversation_id}"))
            self.assertEqual(thread["threadName"], "Fix telemetry labels")
            self.assertEqual(thread["projectLabel"], "Birds-Nest")
            self.assertEqual(thread["totalTokens"], 2677)
            serialized = json.dumps(document)
            self.assertNotIn(str(workspace), serialized)
            self.assertNotIn(conversation_id, serialized)
            self.assertNotIn("outside", serialized)

    def test_conversation_summaries_without_title_column(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            antigravity_dir = root / "antigravity-cli"
            conversations = antigravity_dir / "conversations"
            conversations.mkdir(parents=True)
            workspace = root / "orca" / "RobinGraph" / "task"
            workspace.mkdir(parents=True)
            conversation_id = "3e7a90c5-f0d4-43eb-a0ac-deb65266fca0"
            create_conversation(
                conversations / f"{conversation_id}.db",
                int(datetime(2026, 10, 2, 1, 0, tzinfo=timezone.utc).timestamp()),
            )
            summaries = sqlite3.connect(antigravity_dir / "conversation_summaries.db")
            summaries.execute(
                "CREATE TABLE conversation_summaries (conversation_id TEXT, workspace_uris TEXT)"
            )
            summaries.execute(
                "INSERT INTO conversation_summaries VALUES (?, ?)",
                (conversation_id, json.dumps([workspace.as_uri()])),
            )
            summaries.commit()
            summaries.close()

            document = usage.build_document(antigravity_dir, (root / "orca",))
            thread = document["threadAggregates"][0]
            self.assertIsNone(thread["threadName"])
            self.assertEqual(thread["projectLabel"], "RobinGraph")

    def test_project_label_uses_repository_after_workspaces(self) -> None:
        cases = (
            ("/Volumes/Example/app-data/orca/kimdove/home/workspaces/Gullinkambi/main", "Gullinkambi"),
            ("/Users/example/orca/workspaces/Birds-Nest/main", "Birds-Nest"),
            ("/Volumes/Example/orca/RobinGraph/task", "RobinGraph"),
        )
        for path, expected in cases:
            with self.subTest(path=path):
                self.assertEqual(usage.project_label_for(Path(path)), expected)

    def test_output_is_rewritten_in_place_keeping_the_inode(self) -> None:
        # The telemetry container bind-mounts this single file, so a rename-based
        # replace would leave it looking at a deleted inode.
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "orca-antigravity-usage.json"
            path.write_text("{}", encoding="utf-8")
            inode = path.stat().st_ino
            usage.write_in_place(path, {"dailyAggregates": [], "threadAggregates": []})
            self.assertEqual(path.stat().st_ino, inode)
            self.assertEqual(json.loads(path.read_text())["dailyAggregates"], [])
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)


if __name__ == "__main__":
    unittest.main()
