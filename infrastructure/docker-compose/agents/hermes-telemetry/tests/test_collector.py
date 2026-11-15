from __future__ import annotations

import importlib.util
import json
import os
import sqlite3
import sys
import tempfile
import time
import unittest
from dataclasses import replace
from pathlib import Path


MODULE_PATH = Path(__file__).parents[1] / "collector.py"
SPEC = importlib.util.spec_from_file_location("hermes_telemetry_collector", MODULE_PATH)
assert SPEC and SPEC.loader
collector = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = collector
SPEC.loader.exec_module(collector)


def create_fixture(path: Path, started_at: float) -> None:
    connection = sqlite3.connect(path)
    connection.executescript(
        """
        CREATE TABLE sessions (
            id TEXT PRIMARY KEY,
            source TEXT NOT NULL,
            model TEXT,
            model_config TEXT,
            parent_session_id TEXT,
            started_at REAL NOT NULL,
            ended_at REAL,
            end_reason TEXT,
            message_count INTEGER DEFAULT 0,
            tool_call_count INTEGER DEFAULT 0,
            input_tokens INTEGER DEFAULT 0,
            output_tokens INTEGER DEFAULT 0,
            cache_read_tokens INTEGER DEFAULT 0,
            cache_write_tokens INTEGER DEFAULT 0,
            reasoning_tokens INTEGER DEFAULT 0,
            billing_provider TEXT,
            billing_mode TEXT,
            estimated_cost_usd REAL,
            actual_cost_usd REAL,
            cost_status TEXT,
            pricing_version TEXT,
            api_call_count INTEGER DEFAULT 0,
            rewind_count INTEGER DEFAULT 0
        );
        CREATE TABLE messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT NOT NULL,
            role TEXT NOT NULL,
            content TEXT,
            tool_calls TEXT,
            tool_name TEXT,
            reasoning TEXT
        );
        CREATE TABLE state_meta (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );
        """
    )
    connection.execute(
        """
        INSERT INTO sessions (
            id, source, model, started_at, message_count, tool_call_count,
            input_tokens, output_tokens, billing_provider
        ) VALUES (?, 'cli', 'gpt-test', ?, 2, 1, 100, 20, 'openai-codex')
        """,
        ("session-1", started_at),
    )
    connection.execute(
        """
        INSERT INTO messages (session_id, role, content, tool_calls, reasoning)
        VALUES (?, 'assistant', ?, ?, ?)
        """,
        (
            "session-1",
            "private prompt content",
            json.dumps(
                [
                    {
                        "function": {
                            "name": "terminal",
                            "arguments": json.dumps({"token": "secret-value"}),
                        }
                    }
                ]
            ),
            "private chain of thought",
        ),
    )
    connection.commit()
    connection.close()


def create_kanban_fixture(path: Path, created_at: int) -> None:
    connection = sqlite3.connect(path)
    connection.executescript(
        """
        CREATE TABLE tasks (
            id TEXT PRIMARY KEY,
            title TEXT NOT NULL,
            body TEXT,
            assignee TEXT,
            status TEXT NOT NULL,
            priority INTEGER DEFAULT 0,
            created_by TEXT,
            created_at INTEGER NOT NULL,
            started_at INTEGER,
            completed_at INTEGER,
            workspace_kind TEXT NOT NULL DEFAULT 'scratch',
            workspace_path TEXT,
            branch_name TEXT,
            project_id TEXT,
            claim_lock TEXT,
            claim_expires INTEGER,
            tenant TEXT,
            result TEXT,
            idempotency_key TEXT,
            consecutive_failures INTEGER NOT NULL DEFAULT 0,
            worker_pid INTEGER,
            last_failure_error TEXT,
            max_runtime_seconds INTEGER,
            last_heartbeat_at INTEGER,
            current_run_id INTEGER,
            workflow_template_id TEXT,
            current_step_key TEXT,
            skills TEXT,
            model_override TEXT,
            max_retries INTEGER,
            goal_mode INTEGER NOT NULL DEFAULT 0,
            goal_max_turns INTEGER,
            session_id TEXT,
            block_kind TEXT,
            block_recurrences INTEGER NOT NULL DEFAULT 0
        );
        CREATE TABLE task_runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            task_id TEXT NOT NULL,
            profile TEXT,
            step_key TEXT,
            status TEXT NOT NULL,
            claim_lock TEXT,
            claim_expires INTEGER,
            worker_pid INTEGER,
            max_runtime_seconds INTEGER,
            last_heartbeat_at INTEGER,
            started_at INTEGER NOT NULL,
            ended_at INTEGER,
            outcome TEXT,
            summary TEXT,
            metadata TEXT,
            error TEXT
        );
        CREATE TABLE task_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            task_id TEXT NOT NULL,
            run_id INTEGER,
            kind TEXT NOT NULL,
            payload TEXT,
            created_at INTEGER NOT NULL
        );
        CREATE TABLE task_links (
            parent_id TEXT NOT NULL,
            child_id TEXT NOT NULL,
            PRIMARY KEY (parent_id, child_id)
        );
        CREATE TABLE task_comments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            task_id TEXT NOT NULL,
            author TEXT NOT NULL,
            body TEXT NOT NULL,
            created_at INTEGER NOT NULL
        );
        CREATE TABLE task_attachments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            task_id TEXT NOT NULL,
            filename TEXT NOT NULL,
            stored_path TEXT NOT NULL,
            content_type TEXT,
            size INTEGER NOT NULL DEFAULT 0,
            uploaded_by TEXT,
            created_at INTEGER NOT NULL
        );
        """
    )
    connection.execute(
        """
        INSERT INTO tasks (
            id, title, body, assignee, status, priority, created_by, created_at,
            started_at, workspace_path, result, skills, session_id
        ) VALUES (
            't_secret', 'Sensitive project title', 'private body text',
            'devel', 'running', 2, 'orchestrator', ?, ?, '/private/path',
            'private result text', '["python"]', 'session-secret'
        )
        """,
        (created_at, created_at + 1),
    )
    connection.execute(
        """
        INSERT INTO task_runs (
            task_id, profile, status, started_at, summary, metadata, error
        ) VALUES (
            't_secret', 'devel', 'running', ?, 'private summary',
            '{"rubric":{"correctness":5},"secret":"hidden"}', 'private error'
        )
        """,
        (created_at + 1,),
    )
    connection.execute(
        """
        INSERT INTO task_events (task_id, run_id, kind, payload, created_at)
        VALUES ('t_secret', 1, 'created', '{"token":"secret-value"}', ?)
        """,
        (created_at,),
    )
    connection.execute(
        "INSERT INTO task_comments (task_id, author, body, created_at) VALUES ('t_secret', 'user', 'private comment', ?)",
        (created_at,),
    )
    connection.execute(
        """
        INSERT INTO task_attachments (task_id, filename, stored_path, size, created_at)
        VALUES ('t_secret', 'secret.txt', '/private/secret.txt', 10, ?)
        """,
        (created_at,),
    )
    connection.commit()
    connection.close()


class CollectorTests(unittest.TestCase):
    def settings(self, root: Path) -> object:
        return collector.Settings(
            hermes_home=root,
            state_path=root / "cursor.json",
            webhook_url="https://example.invalid/webhook",
            webhook_token="secret",
            interval_seconds=60,
            request_timeout_seconds=5,
            batch_size=100,
            initial_backfill_hours=24,
            rescan_hours=168,
            verify_tls=True,
            collector_id="test-mac",
            include_kanban_titles=False,
            orca_usage_dir=root / "orca-state",
            collect_orca_usage=False,
        )

    def test_discovers_default_and_named_profiles(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "state.db").touch()
            (root / "profiles" / "researcher").mkdir(parents=True)
            (root / "profiles" / "researcher" / "state.db").touch()
            self.assertEqual(
                [(name, path.relative_to(root).as_posix()) for name, path in collector.discover_databases(root)],
                [("default", "state.db"), ("researcher", "profiles/researcher/state.db")],
            )

    def test_excludes_legacy_profile_databases(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "state.db").touch()
            for name in ("hybrid-v2", "researcher"):
                (root / "profiles" / name).mkdir(parents=True)
                (root / "profiles" / name / "state.db").touch()
            found = collector.discover_databases(
                root, frozenset({"default", "researcher"})
            )
            self.assertEqual(
                [(name, path.relative_to(root).as_posix()) for name, path in found],
                [("hybrid-v2", "profiles/hybrid-v2/state.db")],
            )

    def test_incremental_fingerprint_and_privacy(self) -> None:
        now = 2_000_000_000.0
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            create_fixture(root / "state.db", now - 60)
            settings = self.settings(root)
            state = {"schema_version": collector.SCHEMA_VERSION, "runs": {}}

            first = collector.collect_changes(settings, state, now)
            self.assertEqual(len(first), 1)
            self.assertEqual(first[0]["status"], "open")
            self.assertEqual(first[0]["tool_usage"], {"terminal": 1})
            serialized = json.dumps(first)
            self.assertNotIn("private prompt content", serialized)
            self.assertNotIn("private chain of thought", serialized)
            self.assertNotIn("secret-value", serialized)

            second = collector.collect_changes(settings, state, now + 60)
            self.assertEqual(second, [])

            connection = sqlite3.connect(root / "state.db")
            connection.execute(
                """
                UPDATE sessions
                SET ended_at = ?, end_reason = 'agent_close', output_tokens = 40
                WHERE id = 'session-1'
                """,
                (now + 120,),
            )
            connection.commit()
            connection.close()

            third = collector.collect_changes(settings, state, now + 180)
            self.assertEqual(len(third), 1)
            self.assertEqual(third[0]["status"], "completed")
            self.assertEqual(third[0]["output_tokens"], 40)
            self.assertEqual(third[0]["duration_ms"], 180_000)

    def test_failure_status_is_inferred_from_end_reason(self) -> None:
        self.assertEqual(collector.session_status(10, "gateway_crashed"), "failed")
        self.assertEqual(collector.session_status(10, "cron_complete"), "completed")
        self.assertEqual(collector.session_status(None, None), "open")

    def test_orca_usage_day_uses_dashboard_timezone(self) -> None:
        self.assertEqual(collector.usage_day("2026-09-13T16:00:00Z"), "2026-09-14")

    def test_goal_changes_are_fingerprinted_and_exclude_goal_text(self) -> None:
        now = 2_000_000_000.0
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            create_fixture(root / "state.db", now - 600)
            connection = sqlite3.connect(root / "state.db")
            connection.execute(
                "INSERT INTO state_meta (key, value) VALUES (?, ?)",
                (
                    "goal:session-1",
                    json.dumps(
                        {
                            "goal": "private goal text",
                            "status": "active",
                            "turns_used": 1,
                            "max_turns": 8,
                            "created_at": now - 300,
                            "last_turn_at": now - 120,
                            "last_verdict": "continue",
                            "last_reason": "private judge reason",
                        }
                    ),
                ),
            )
            connection.execute(
                "INSERT INTO sessions (id, source, model, started_at) VALUES ('session-old', 'cli', 'gpt-test', ?)",
                (now - 600,),
            )
            connection.execute(
                "INSERT INTO state_meta (key, value) VALUES (?, ?)",
                (
                    "goal:session-old",
                    json.dumps(
                        {
                            "goal": "private migrated goal text",
                            "status": "cleared",
                            "turns_used": 0,
                            "max_turns": 8,
                            "created_at": now - 300,
                            "last_turn_at": 0,
                        }
                    ),
                ),
            )
            connection.commit()
            connection.close()

            settings = self.settings(root)
            state = {"schema_version": collector.SCHEMA_VERSION, "runs": {}, "goals": {}}

            first = collector.collect_goal_changes(settings, state)
            self.assertEqual(len(first), 1)
            self.assertEqual(first[0]["profile"], "default")
            self.assertEqual(first[0]["model"], "gpt-test")
            self.assertEqual(first[0]["status"], "active")
            self.assertIsNone(first[0]["ended_at"])
            self.assertIsNone(first[0]["duration_ms"])
            self.assertNotIn("private goal text", json.dumps(first))
            self.assertNotIn("private judge reason", json.dumps(first))
            self.assertEqual(collector.collect_goal_changes(settings, state), [])

            connection = sqlite3.connect(root / "state.db")
            goal_state = json.loads(
                connection.execute(
                    "SELECT value FROM state_meta WHERE key = 'goal:session-1'"
                ).fetchone()[0]
            )
            goal_state.update(
                {
                    "status": "paused",
                    "turns_used": 8,
                    "last_turn_at": now,
                    "paused_reason": "turn budget exhausted (8/8)",
                }
            )
            connection.execute(
                "UPDATE state_meta SET value = ? WHERE key = 'goal:session-1'",
                (json.dumps(goal_state),),
            )
            connection.commit()
            connection.close()

            completed = collector.collect_goal_changes(settings, state)
            self.assertEqual(len(completed), 1)
            self.assertEqual(completed[0]["status"], "paused")
            self.assertEqual(completed[0]["turns_used"], 8)
            self.assertEqual(completed[0]["duration_ms"], 300_000)

    def test_kanban_changes_are_fingerprinted_and_privacy_safe(self) -> None:
        now = 2_000_000_000.0
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            board = root / "kanban" / "boards" / "general-research"
            board.mkdir(parents=True)
            create_kanban_fixture(board / "kanban.db", int(now - 60))
            settings = self.settings(root)
            state = {"schema_version": collector.SCHEMA_VERSION, "runs": {}}

            first = collector.collect_kanban_changes(settings, state)
            self.assertEqual(len(first["kanban_tasks"]), 1)
            self.assertEqual(len(first["kanban_task_runs"]), 1)
            self.assertEqual(len(first["kanban_task_events"]), 1)
            self.assertEqual(first["kanban_tasks"][0]["board"], "general-research")
            self.assertIsNone(first["kanban_tasks"][0]["title"])
            self.assertTrue(first["kanban_tasks"][0]["result_present"])
            self.assertEqual(first["kanban_tasks"][0]["skill_count"], 1)
            self.assertEqual(first["kanban_tasks"][0]["comment_count"], 1)
            self.assertEqual(first["kanban_task_runs"][0]["metadata_keys"], ["rubric", "secret"])
            serialized = json.dumps(first)
            self.assertNotIn("private body text", serialized)
            self.assertNotIn("private result text", serialized)
            self.assertNotIn("private comment", serialized)
            self.assertNotIn("/private/path", serialized)
            self.assertNotIn("secret-value", serialized)

            second = collector.collect_kanban_changes(settings, state)
            self.assertEqual(second["kanban_tasks"], [])
            self.assertEqual(second["kanban_task_runs"], [])
            self.assertEqual(second["kanban_task_events"], [])

            connection = sqlite3.connect(board / "kanban.db")
            connection.execute("UPDATE tasks SET status = 'done', completed_at = ? WHERE id = 't_secret'", (int(now),))
            connection.commit()
            connection.close()

            third = collector.collect_kanban_changes(settings, state)
            self.assertEqual(len(third["kanban_tasks"]), 1)
            self.assertEqual(third["kanban_tasks"][0]["status"], "done")

    def test_orca_usage_is_normalized_incrementally_and_without_paths(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            usage_dir = root / "orca-state"
            usage_dir.mkdir()
            (usage_dir / "orca-codex-usage.json").write_text(
                json.dumps(
                    {
                        "schemaVersion": 5,
                        "dailyAggregates": [
                            {
                                "day": "2025-09-14",
                                "model": "gpt-test",
                                "projectKey": "cwd:/Users/private/secret-repo",
                                "projectLabel": "/Users/private/secret-repo",
                                "worktreeId": "repo:test",
                                "eventCount": 3,
                                "inputTokens": 1000,
                                "cachedInputTokens": 800,
                                "outputTokens": 100,
                                "reasoningOutputTokens": 25,
                                "totalTokens": 1100,
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            (usage_dir / "orca-claude-usage.json").write_text(
                json.dumps(
                    {
                        "schemaVersion": 6,
                        "dailyAggregates": [
                            {
                                "day": "2025-09-14",
                                "model": "claude-test",
                                "projectKey": "/Users/private/secret-repo",
                                "projectLabel": "secret-repo",
                                "worktreeId": "repo:test",
                                "turnCount": 2,
                                "inputTokens": 10,
                                "cacheReadTokens": 500,
                                "cacheWriteTokens": 50,
                                "cacheWrite1hTokens": 40,
                                "outputTokens": 90,
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )

            settings = replace(
                self.settings(root),
                orca_usage_dir=usage_dir,
                collect_orca_usage=True,
            )
            state = {"schema_version": collector.SCHEMA_VERSION, "orca_usage": {}}

            first = collector.collect_orca_usage_changes(settings, state)
            self.assertEqual(len(first), 2)
            codex = next(item for item in first if item["agent"] == "codex")
            claude = next(item for item in first if item["agent"] == "claude")
            self.assertEqual(codex["total_tokens"], 1100)
            self.assertEqual(codex["reasoning_tokens"], 25)
            self.assertEqual(claude["total_tokens"], 650)
            self.assertEqual(claude["request_count"], 2)
            self.assertEqual(claude["project_label"], "secret-repo")
            serialized = json.dumps(first)
            self.assertNotIn("/Users/private", serialized)
            self.assertNotIn("cwd:", serialized)
            self.assertEqual(collector.collect_orca_usage_changes(settings, state), [])

            document = json.loads((usage_dir / "orca-codex-usage.json").read_text())
            document["dailyAggregates"][0]["outputTokens"] = 120
            document["dailyAggregates"][0]["totalTokens"] = 1120
            (usage_dir / "orca-codex-usage.json").write_text(json.dumps(document))
            changed = collector.collect_orca_usage_changes(settings, state)
            self.assertEqual(len(changed), 1)
            self.assertEqual(changed[0]["total_tokens"], 1120)

    def test_orca_project_label_uses_repository_not_worktree(self) -> None:
        cases = (
            (
                "dev",
                "worktree:abc::/Users/example/orca/workspaces/RobinGraph/dev",
                "RobinGraph",
            ),
            (
                "recover-orca-bridge-v2-43ec0779",
                "/Users/example/orca/workspaces/Birds-Nest/recover-orca-bridge-v2-43ec0779",
                "Birds-Nest",
            ),
            ("RobinGraph", "/Volumes/Example/orca/RobinGraph", "RobinGraph"),
            (
                "main",
                "/Volumes/Example/app-data/orca/kimdove/home/workspaces/Gullinkambi/main",
                "Gullinkambi",
            ),
            ("secret-repo", "cwd:/Users/private/secret-repo", "secret-repo"),
        )
        for label, key, expected in cases:
            with self.subTest(key=key):
                self.assertEqual(collector.privacy_safe_project_label(label, key), expected)

    def test_skill_names_require_a_skill_file_read_or_explicit_call(self) -> None:
        path = "/Users/example/.codex/skills/openai-docs/SKILL.md"
        self.assertEqual(
            collector.skill_names_from_tool_call("exec_command", {"cmd": f"sed -n '1,80p' {path}"}),
            {"openai-docs"},
        )
        self.assertEqual(
            collector.skill_names_from_tool_call("Read", {"file_path": path}),
            {"openai-docs"},
        )
        self.assertEqual(
            collector.skill_names_from_tool_call("Skill", {"skill": "openai-docs"}),
            {"openai-docs"},
        )
        self.assertEqual(
            collector.skill_names_from_tool_call("exec_command", {"cmd": f"rg SKILL.md {path}"}),
            set(),
        )
        self.assertEqual(
            collector.skill_names_from_tool_call("Edit", {"file_path": path}),
            set(),
        )

    def test_orca_project_label_revision_replays_existing_rows(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            usage_dir = root / "orca-state"
            usage_dir.mkdir()
            (usage_dir / "orca-codex-usage.json").write_text(
                json.dumps({"dailyAggregates": [{
                    "day": "2025-09-14",
                    "model": "gpt-test",
                    "projectKey": "worktree:abc::/Users/example/orca/workspaces/RobinGraph/dev",
                    "projectLabel": "dev",
                    "worktreeId": "abc",
                    "eventCount": 1,
                    "inputTokens": 10,
                }]}),
                encoding="utf-8",
            )
            settings = replace(self.settings(root), orca_usage_dir=usage_dir, collect_orca_usage=True)
            state = {"orca_usage_revision": 0, "orca_usage": {"stale": {}}, "orca_raw_files": {"stale": {}}}
            rows = collector.collect_orca_usage_changes(settings, state)
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["project_label"], "RobinGraph")
            self.assertEqual(state["orca_usage_revision"], collector.ORCA_USAGE_REVISION)
            self.assertEqual(collector.collect_orca_usage_changes(settings, state), [])

    def test_orca_project_label_revision_replays_old_session_files(self) -> None:
        now = time.time()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            usage_dir = root / "orca-state"
            codex_dir = root / "codex"
            usage_dir.mkdir()
            codex_dir.mkdir()
            (usage_dir / "orca-codex-usage.json").write_text(
                json.dumps({"dailyAggregates": []}), encoding="utf-8"
            )
            session = codex_dir / "old-session.jsonl"
            session.write_text("\n".join(json.dumps(event) for event in (
                {"type": "session_meta", "payload": {"cwd": "/work/orca/workspaces/RobinGraph/dev"}},
                {"type": "turn_context", "payload": {"model": "gpt-test"}},
                {"type": "token_usage_record", "timestamp": "2025-09-14T12:00:00Z",
                 "payload": {"response_id": "response-1", "usage": {"input_tokens": 10}}},
            )) + "\n", encoding="utf-8")
            os.utime(session, (now - 30 * 86400, now - 30 * 86400))
            settings = replace(
                self.settings(root), orca_usage_dir=usage_dir,
                orca_codex_sessions_dir=codex_dir,
                orca_worktree_roots=(Path("/work/orca"),),
                collect_orca_usage=True,
            )
            state = {"orca_usage_revision": 1, "orca_usage": {"stale": {}}}
            rows = collector.collect_orca_usage_changes(settings, state, now)
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["project_label"], "RobinGraph")

    def test_antigravity_usage_is_normalized(self) -> None:
        item = collector.normalize_orca_usage(
            "antigravity",
            {
                "day": "2026-10-01",
                "model": "gemini-3.8-flash-high",
                "projectKey": "privacy-safe-hash",
                "projectLabel": "dev-mac",
                "requestCount": 2,
                "inputTokens": 21155,
                "cacheReadTokens": 16295,
                "outputTokens": 200,
                "reasoningTokens": 198,
                "totalTokens": 21355,
            },
        )
        self.assertIsNotNone(item)
        assert item is not None
        self.assertEqual(item["agent"], "antigravity")
        self.assertEqual(item["request_count"], 2)
        self.assertEqual(item["cache_read_tokens"], 16295)
        self.assertEqual(item["reasoning_tokens"], 198)
        self.assertEqual(item["total_tokens"], 21355)

    def test_antigravity_thread_aggregates_become_thread_usage(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            usage_dir = root / "orca-state"
            usage_dir.mkdir()
            thread_id = "0123456789abcdef01234567"
            base = {
                "day": "2026-10-02",
                "model": "gemini-3.8-flash",
                "projectKey": "privacy-safe-hash",
                "projectLabel": "RobinGraph",
                "requestCount": 3,
                "inputTokens": 100,
                "outputTokens": 20,
                "totalTokens": 120,
            }
            (usage_dir / collector.ORCA_USAGE_FILES["antigravity"]).write_text(
                json.dumps(
                    {
                        "dailyAggregates": [],
                        "threadAggregates": [
                            {**base, "threadId": thread_id, "threadName": "Map   bird cards"},
                            {**base, "threadId": "not-a-hash"},
                        ],
                    }
                ),
                encoding="utf-8",
            )
            settings = replace(self.settings(root), orca_usage_dir=usage_dir, collect_orca_usage=True)

            rows = collector.collect_orca_thread_usage_changes(settings, {}, time.time())
            self.assertEqual(len(rows), 1)
            row = rows[0]
            self.assertEqual(row["agent"], "antigravity")
            self.assertEqual(row["thread_id"], thread_id)
            self.assertEqual(row["thread_name"], "Map bird cards")
            self.assertEqual(row["project_label"], "RobinGraph")
            self.assertEqual(row["total_tokens"], 120)
            self.assertTrue(row["event_id"].startswith(f"orca-thread:antigravity:{thread_id}:2026-10-02:"))
            self.assertIn("fingerprint", row)

    def test_raw_orca_sessions_are_current_deduplicated_and_scoped(self) -> None:
        now = time.time()
        timestamp = collector.utc_iso(now)
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            usage_dir = root / "orca-state"
            codex_dir = root / "codex"
            claude_dir = root / "claude"
            usage_dir.mkdir()
            codex_dir.mkdir()
            claude_dir.mkdir()
            for filename in collector.ORCA_USAGE_FILES.values():
                (usage_dir / filename).write_text(
                    json.dumps({"dailyAggregates": []}), encoding="utf-8"
                )

            codex_events = [
                {
                    "type": "session_meta",
                    "timestamp": timestamp,
                    "payload": {"cwd": "/work/orca/repo/task"},
                },
                {
                    "type": "turn_context",
                    "timestamp": timestamp,
                    "payload": {"cwd": "/work/orca/repo/task", "model": "gpt-test"},
                },
                {
                    "type": "token_usage_record",
                    "timestamp": timestamp,
                    "payload": {
                        "response_id": "response-1",
                        "usage": {
                            "input_tokens": 100,
                            "cached_input_tokens": 80,
                            "output_tokens": 20,
                            "reasoning_output_tokens": 5,
                            "total_tokens": 120,
                        },
                    },
                },
            ]
            (codex_dir / "session.jsonl").write_text(
                "".join(json.dumps(event) + "\n" for event in codex_events),
                encoding="utf-8",
            )

            claude_event = {
                "type": "assistant",
                "timestamp": timestamp,
                "cwd": "/work/orca/repo/task",
                "message": {
                    "id": "message-1",
                    "model": "claude-test",
                    "usage": {
                        "input_tokens": 2,
                        "cache_creation_input_tokens": 30,
                        "cache_read_input_tokens": 40,
                        "output_tokens": 10,
                    },
                },
            }
            (claude_dir / "session.jsonl").write_text(
                json.dumps(claude_event) + "\n" + json.dumps(claude_event) + "\n",
                encoding="utf-8",
            )

            settings = replace(
                self.settings(root),
                orca_usage_dir=usage_dir,
                orca_codex_sessions_dir=codex_dir,
                orca_claude_projects_dir=claude_dir,
                orca_worktree_roots=(Path("/work/orca"),),
                collect_orca_usage=True,
            )
            state = {"schema_version": collector.SCHEMA_VERSION}
            first = collector.collect_orca_usage_changes(settings, state, now)
            self.assertEqual(len(first), 2)
            codex = next(item for item in first if item["agent"] == "codex")
            claude = next(item for item in first if item["agent"] == "claude")
            self.assertEqual(codex["request_count"], 1)
            self.assertEqual(codex["total_tokens"], 120)
            self.assertEqual(claude["request_count"], 1)
            self.assertEqual(claude["total_tokens"], 82)
            self.assertEqual(collector.collect_orca_usage_changes(settings, state, now), [])

            cached = state["orca_raw_files"]["codex"]
            self.assertEqual(len(cached), 1)
            self.assertNotIn("/work/orca", json.dumps(cached))

    def test_orca_thread_usage_backfills_old_sessions_and_keeps_threads_separate(self) -> None:
        now = time.time()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            codex_dir = root / "codex"
            codex_dir.mkdir()
            state_db = root / "state_5.sqlite"
            thread_ids = (
                "11111111-1111-1111-1111-111111111111",
                "22222222-2222-2222-2222-222222222222",
            )
            connection = sqlite3.connect(state_db)
            connection.execute("CREATE TABLE threads (id TEXT, title TEXT, name TEXT)")
            connection.executemany(
                "INSERT INTO threads VALUES (?, ?, ?)",
                [(thread_ids[0], "Generated title", "Named thread"),
                 (thread_ids[1], "Fallback title", None)],
            )
            connection.commit()
            connection.close()
            for thread_id, tokens in zip(thread_ids, (120, 240)):
                events = [
                    {"type": "session_meta", "payload": {"cwd": "/work/orca/repo"}},
                    {"type": "turn_context", "payload": {"model": "gpt-test"}},
                    {"type": "response_item", "payload": {
                        "type": "function_call", "name": "exec_command",
                        "arguments": json.dumps({"cmd": "cat /home/test/skills/example-skill/SKILL.md"}),
                    }} if thread_id == thread_ids[0] else {"type": "event_msg", "payload": {}},
                    {
                        "type": "token_usage_record",
                        "timestamp": "2025-09-14T10:00:00Z",
                        "payload": {
                            "response_id": f"response-{thread_id}",
                            "usage": {"input_tokens": tokens - 20,
                                      "output_tokens": 20,
                                      "total_tokens": tokens},
                        },
                    },
                ]
                path = codex_dir / f"rollout-{thread_id}.jsonl"
                path.write_text("".join(json.dumps(event) + "\n" for event in events))
                os.utime(path, (now - 30 * 86400, now - 30 * 86400))
            settings = replace(
                self.settings(root),
                orca_codex_sessions_dir=codex_dir,
                orca_codex_state_db=state_db,
                orca_claude_projects_dir=None,
                orca_worktree_roots=(Path("/work/orca"),),
                collect_orca_usage=True,
            )
            state = {"schema_version": collector.SCHEMA_VERSION}
            rows = collector.collect_orca_thread_usage_changes(settings, state, now)
            self.assertEqual({row["thread_id"]: row["total_tokens"] for row in rows},
                             dict(zip(thread_ids, (120, 240))))
            self.assertEqual(
                {row["thread_id"]: row["thread_name"] for row in rows},
                dict(zip(thread_ids, ("Named thread", "Fallback title"))),
            )
            self.assertEqual(
                {row["thread_id"]: row["skill_names"] for row in rows},
                {thread_ids[0]: ["example-skill"], thread_ids[1]: []},
            )
            self.assertEqual(collector.collect_orca_thread_usage_changes(settings, state, now), [])
            state["orca_thread_usage_revision"] = 1
            replayed = collector.collect_orca_thread_usage_changes(settings, state, now)
            self.assertEqual(len(replayed), 2)
            self.assertEqual(collector.collect_orca_thread_usage_changes(settings, state, now), [])
            self.assertNotIn("/work/orca", json.dumps(rows))

    def test_claude_thread_title_is_attached_to_usage(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "33333333-3333-3333-3333-333333333333.jsonl"
            events = [
                {"type": "ai-title", "aiTitle": "  Claude\nthread  "},
                {"type": "assistant", "timestamp": "2025-09-14T10:00:00Z",
                 "cwd": "/work/orca/repo", "message": {
                     "id": "message-1", "model": "claude-test",
                     "content": [{"type": "tool_use", "name": "Read", "input": {
                         "file_path": "/home/test/skills/claude-skill/SKILL.md"}}],
                     "usage": {"input_tokens": 2, "output_tokens": 3}}},
            ]
            path.write_text("".join(json.dumps(event) + "\n" for event in events))
            rows = collector.parse_claude_usage_file(
                path, (Path("/work/orca"),), path.stem
            )
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["thread_name"], "Claude thread")
            self.assertEqual(rows[0]["skill_names"], ["claude-skill"])


if __name__ == "__main__":
    unittest.main()
