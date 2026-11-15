import dataclasses
import json
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE))

import collector  # noqa: E402
import hermes_sessions as hs  # noqa: E402

NOW = 1_800_000_000.0


def make_db(path: Path) -> sqlite3.Connection:
    db = sqlite3.connect(path)
    db.executescript("""
        CREATE TABLE sessions (id TEXT PRIMARY KEY, source TEXT NOT NULL, chat_type TEXT, model TEXT,
            parent_session_id TEXT, started_at REAL NOT NULL, ended_at REAL, end_reason TEXT,
            expiry_finalized INTEGER DEFAULT 0, archived INTEGER DEFAULT 0, cwd TEXT, billing_provider TEXT,
            estimated_cost_usd REAL, actual_cost_usd REAL, cost_status TEXT, message_count INTEGER DEFAULT 0,
            tool_call_count INTEGER DEFAULT 0, api_call_count INTEGER DEFAULT 0, input_tokens INTEGER DEFAULT 0,
            output_tokens INTEGER DEFAULT 0, cache_read_tokens INTEGER DEFAULT 0, cache_write_tokens INTEGER DEFAULT 0,
            reasoning_tokens INTEGER DEFAULT 0, title TEXT, display_name TEXT, chat_id TEXT, user_id TEXT);
        CREATE TABLE messages (id INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT, role TEXT, content TEXT,
            tool_name TEXT, timestamp REAL);
    """)
    return db


class ToolOutcomeTest(unittest.TestCase):
    def test_classification(self):
        self.assertEqual(hs.tool_outcome('{"exit_code": 0, "error": null}'), "ok")
        self.assertEqual(hs.tool_outcome('{"exit_code": 1, "error": null}'), "nonzero_exit")
        self.assertEqual(hs.tool_outcome('{"success": false, "error": "boom"}'), "error")
        self.assertEqual(hs.tool_outcome('{"error": "denied"}'), "error")
        self.assertEqual(hs.tool_outcome('{"success": true}'), "ok")
        self.assertEqual(hs.tool_outcome('{"content": "file body", "file_size": 3}'), "ok")
        self.assertEqual(hs.tool_outcome('{"matches": [], "total_count": 0}'), "ok")
        self.assertEqual(hs.tool_outcome('{"status": "error", "output": ""}'), "error")
        self.assertEqual(hs.tool_outcome('{"status": "completed", "error": null}'), "ok")
        self.assertEqual(hs.tool_outcome("[1, 2]"), "unknown")
        self.assertEqual(hs.tool_outcome('{"content": "cut off'), "unknown")
        self.assertEqual(hs.tool_outcome("plain text"), "unknown")
        self.assertEqual(hs.tool_outcome(None), "unknown")


class CollectorTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        home = Path(self.tmp.name)
        (home / "profiles" / "p1").mkdir(parents=True)
        self.db = make_db(home / "profiles" / "p1" / "state.db")
        self.db.execute("INSERT INTO sessions (id, source, chat_type, model, started_at, input_tokens, output_tokens, "
                        "message_count, title, display_name, chat_id, user_id) VALUES "
                        "('SECRET-ID','discord','thread','m',?,100,5,3,'secret title','secret name','chat-1','user-1')",
                        (NOW - 600,))
        self.db.execute("INSERT INTO messages (session_id, role, content, tool_name, timestamp) VALUES "
                        "('SECRET-ID','tool','{\"exit_code\": 0}','terminal',?)", (NOW - 300,))
        self.db.execute("INSERT INTO messages (session_id, role, content, tool_name, timestamp) VALUES "
                        "('SECRET-ID','tool','SECRET RESULT TEXT','mcp__obsidian_vault__vault',?)", (NOW - 200,))
        self.db.commit()
        base = collector.Settings.from_env()
        self.settings = dataclasses.replace(base, hermes_home=home, collect_orca_usage=False)

    def run_once(self, state):
        return collector.collect_hermes_session_changes(self.settings, state, NOW)

    def test_first_run_emits_sample_and_tool_events_without_private_data(self):
        out = self.run_once({})
        (sample,) = out["hermes_session_samples"]
        self.assertEqual(sample["input_tokens"], 100)
        self.assertEqual(sample["last_activity_at"], hs._iso(NOW - 200))
        self.assertEqual([e["tool"] for e in out["hermes_tool_events"]], ["terminal", "mcp:obsidian_vault"])
        self.assertEqual([e["outcome"] for e in out["hermes_tool_events"]], ["ok", "unknown"])
        blob = json.dumps(out)
        for secret in ("SECRET", "secret", "chat-1", "user-1"):
            self.assertNotIn(secret, blob)

    def test_unchanged_session_is_not_resent_and_tool_cursor_advances(self):
        state = {}
        self.run_once(state)
        again = self.run_once(state)
        self.assertEqual(again, {"hermes_session_samples": [], "hermes_tool_events": [], "hermes_tool_log_events": []})

    def test_counter_change_emits_new_sample_and_new_tools_only(self):
        state = {}
        self.run_once(state)
        self.db.execute("UPDATE sessions SET input_tokens = 250, output_tokens = 9")
        self.db.execute("INSERT INTO messages (session_id, role, content, tool_name, timestamp) VALUES "
                        "('SECRET-ID','tool','{\"success\": false}','patch',?)", (NOW - 10,))
        self.db.commit()
        out = self.run_once(state)
        self.assertEqual(out["hermes_session_samples"][0]["input_tokens"], 250)
        self.assertEqual([(e["tool"], e["outcome"]) for e in out["hermes_tool_events"]], [("patch", "error")])

    def test_changed_outcome_rules_resend_tool_events_once(self):
        state = {}
        self.run_once(state)
        self.assertEqual(self.run_once(state)["hermes_tool_events"], [])
        state["hermes_outcome_rules"] = 0  # as if saved by an older collector
        self.assertEqual(len(self.run_once(state)["hermes_tool_events"]), 2)
        self.assertEqual(self.run_once(state)["hermes_tool_events"], [])

    def test_disabled_collects_nothing(self):
        settings = dataclasses.replace(self.settings, collect_hermes_sessions=False)
        out = collector.collect_hermes_session_changes(settings, {}, NOW)
        self.assertEqual(out, {"hermes_session_samples": [], "hermes_tool_events": [], "hermes_tool_log_events": []})


class ToolLogTest(unittest.TestCase):
    OK = "2026-10-10 05:00:01,250 INFO [20261010_a] agent.tool_executor: tool read_file completed (0.05s, 1234 chars)\n"
    ERR = ('2026-10-10 05:00:02,500 WARNING [20261010_a] agent.tool_executor: Tool mcp__obsidian_vault__vault '
           'returned error (1.20s): {"error": "SECRET DETAIL"}\n')
    OTHER = "2026-10-10 05:00:03,000 INFO [20261010_a] agent.chat_completion_helpers: unrelated\n"

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)
        self.label = collector._orca_calls_module().tool_label

    def read(self, offsets, limit=100):
        return hs.read_tool_log_events(self.dir, "p1", offsets, 0, limit, self.label)

    def test_parses_ok_and_error_without_error_text(self):
        (self.dir / "agent.log").write_text(self.OK + self.OTHER + self.ERR)
        ok, err = self.read({})
        self.assertEqual((ok["tool"], ok["status"], ok["duration_ms"], ok["result_chars"]), ("read_file", "ok", 50, 1234))
        self.assertEqual((err["tool"], err["status"], err["duration_ms"], err["result_chars"]), ("mcp:obsidian_vault", "error", 1200, None))
        self.assertEqual(ok["occurred_at"], "2026-10-10T05:00:01.250000Z")
        self.assertNotIn("SECRET", json.dumps([ok, err]))
        self.assertNotIn("20261010_a", json.dumps([ok, err]))  # the session id is hashed

    def test_offsets_resume_and_partial_line_waits(self):
        path = self.dir / "agent.log"
        path.write_text(self.OK)
        offsets = {}
        self.assertEqual(len(self.read(offsets)), 1)
        self.assertEqual(self.read(offsets), [])
        with path.open("a") as handle:
            handle.write(self.ERR[:40])  # half-written line
        self.assertEqual(self.read(offsets), [])
        with path.open("a") as handle:
            handle.write(self.ERR[40:])
        self.assertEqual([e["status"] for e in self.read(offsets)], ["error"])

    def test_limit_leaves_the_rest_for_the_next_run(self):
        (self.dir / "agent.log").write_text(self.OK + self.ERR)
        offsets = {}
        self.assertEqual([e["status"] for e in self.read(offsets, limit=1)], ["ok"])
        self.assertEqual([e["status"] for e in self.read(offsets, limit=1)], ["error"])

    def test_rotation_continues_from_the_old_offset(self):
        path = self.dir / "agent.log"
        path.write_text(self.OK)
        offsets = {}
        self.read(offsets)
        with path.open("a") as handle:
            handle.write(self.ERR)  # written just before rotation
        path.rename(self.dir / "agent.log.1")
        (self.dir / "agent.log").write_text(self.OK.replace("read_file", "patch"))
        self.assertEqual([e["tool"] for e in self.read(offsets)], ["mcp:obsidian_vault", "patch"])


if __name__ == "__main__":
    unittest.main()
