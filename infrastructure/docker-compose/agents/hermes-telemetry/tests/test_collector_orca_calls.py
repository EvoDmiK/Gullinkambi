from __future__ import annotations

import importlib.util
import json
import os
import re
import sys
import tempfile
import time
import unittest
from dataclasses import replace
from pathlib import Path

HERE = Path(__file__).parent
COLLECTOR = HERE.parent / "collector.py"
SPEC = importlib.util.spec_from_file_location("collector_for_calls", COLLECTOR)
collector = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = collector
SPEC.loader.exec_module(collector)

USAGE = {"input_tokens": 100, "cached_input_tokens": 60, "cache_write_input_tokens": 0,
         "output_tokens": 5, "reasoning_output_tokens": 1, "total_tokens": 105}


def codex_log(session, count, cwd, start=0):
    lines = [json.dumps({"type": "session_meta", "payload": {"id": session, "cwd": cwd}}),
             json.dumps({"type": "turn_context", "payload": {"cwd": cwd, "model": "gpt-x"}})]
    for i in range(start, start + count):
        lines.append(json.dumps({"type": "token_usage_record", "timestamp": "2026-10-09T01:00:00Z",
                                 "payload": {"response_id": f"{session}-{i}", "session_id": session, "usage": USAGE}}))
    return "\n".join(lines) + "\n"


class CollectOrcaCallsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        self.codex, self.claude, self.worktrees = root / "codex", root / "claude", root / "wt"
        for d in (self.codex, self.claude, self.worktrees):
            d.mkdir()
        self.cwd = str(self.worktrees / "repo" / "main")
        self.settings = replace(
            collector.Settings.from_env(),
            collect_orca_calls=True, orca_codex_sessions_dir=self.codex,
            orca_claude_projects_dir=self.claude, orca_worktree_roots=(self.worktrees,),
            orca_call_batch_limit=500, orca_call_lookback_hours=48)
        self.state: dict = {}
        self.log = self.codex / "s1.jsonl"

    def collect(self, now=None):
        return collector.collect_orca_call_changes(self.settings, self.state, now or time.time())

    def test_disabled_by_default(self):
        self.assertFalse(collector.Settings.from_env().collect_orca_calls)
        self.log.write_text(codex_log("s1", 2, self.cwd))
        self.assertEqual(collector.collect_orca_call_changes(replace(self.settings, collect_orca_calls=False), self.state), [])
        self.assertEqual(self.state, {})

    def test_sends_only_changes_and_resends_nothing_on_rerun(self):
        self.log.write_text(codex_log("s1", 3, self.cwd))
        first = self.collect()
        self.assertEqual(len(first), 3)
        self.assertEqual(self.collect(), [])
        with self.log.open("a") as handle:  # session grows: only the new call is sent
            handle.write(codex_log("s1", 1, self.cwd, start=3).split("\n", 2)[2])
        self.assertEqual(len(self.collect()), 1)

    def test_out_of_scope_and_old_files_are_skipped(self):
        self.log.write_text(codex_log("s1", 2, "/somewhere/else"))
        self.assertEqual(self.collect(), [])
        old = self.codex / "old.jsonl"
        old.write_text(codex_log("old", 2, self.cwd))
        past = time.time() - 72 * 3600
        os.utime(old, (past, past))
        self.assertEqual(self.collect(), [])

    def test_batch_limit_is_respected_and_remainder_follows_without_loss(self):
        self.settings = replace(self.settings, orca_call_batch_limit=4)
        self.log.write_text(codex_log("s1", 10, self.cwd))
        seen = []
        for _ in range(5):
            seen += [e["event_key"] for e in self.collect()]
        self.assertEqual(len(seen), 10)
        self.assertEqual(len(set(seen)), 10)
        self.assertEqual(self.collect(), [])

    def test_limit_spans_files_and_marks_only_finished_files(self):
        self.settings = replace(self.settings, orca_call_batch_limit=5)
        other = self.codex / "s2.jsonl"
        self.log.write_text(codex_log("s1", 3, self.cwd))
        other.write_text(codex_log("s2", 3, self.cwd))
        os.utime(self.log, (time.time() - 100, time.time() - 100))
        first = self.collect()
        self.assertEqual(len(first), 5)
        self.assertEqual(len(self.collect()), 1)
        self.assertEqual(self.collect(), [])

    def test_events_carry_no_paths_text_or_raw_ids(self):
        self.log.write_text(codex_log("session-secret-id", 1, self.cwd))
        blob = json.dumps(self.collect())
        for marker in ("session-secret-id", str(self.worktrees), "/repo/main"):
            self.assertNotIn(marker, blob)

    def test_payload_has_the_new_array_and_required_fields(self):
        self.log.write_text(codex_log("s1", 1, self.cwd))
        # other collectors need real dirs; only check the call array is wired in
        events = collector.collect_orca_call_changes(self.settings, self.state, time.time())
        required = ["event_key", "agent", "model", "origin_namespace", "session_id_hash",
                    "source_request_id_hash", "project_key", "fingerprint"]
        self.assertTrue(all(all(e.get(k) for k in required) for e in events))
        self.assertIn('"orca_call_events": collect_orca_call_changes', COLLECTOR.read_text())

    def test_a_late_tool_result_is_sent_once_more_and_old_rows_get_their_tool_info(self):
        def block(use_id, stop="tool_use"):
            return json.dumps({"type": "assistant", "cwd": self.cwd, "sessionId": "s9", "timestamp": "2026-10-09T01:00:00Z",
                               "message": {"id": "m1", "model": "claude-x", "stop_reason": stop,
                                           "usage": {"input_tokens": 1, "cache_read_input_tokens": 9, "cache_creation_input_tokens": 0, "output_tokens": 2},
                                           "content": [{"type": "tool_use", "id": use_id, "name": "Bash", "input": {"command": "SECRET"}}]}})
        log = self.claude / "p" / "s9.jsonl"
        log.parent.mkdir()
        log.write_text(block("u1") + "\n")
        first = self.collect()
        self.assertEqual([(e["tool_calls"], e["tool_failures"]) for e in first], [({"Bash": 1}, 0)])
        self.assertEqual(self.collect(), [])  # nothing changed -> nothing resent
        with log.open("a") as handle:  # the tool result arrives later and it failed
            handle.write(json.dumps({"type": "user", "message": {"content": [{"type": "tool_result", "tool_use_id": "u1", "is_error": True}]}}) + "\n")
        again = self.collect()
        self.assertEqual([(e["tool_calls"], e["tool_failures"]) for e in again], [({"Bash": 1}, 1)])
        self.assertGreater(again[0]["revision"], first[0]["revision"])
        self.assertNotIn("SECRET", json.dumps(again))

    def test_changed_event_shape_changes_the_fingerprint_so_history_is_resent(self):
        self.log.write_text(codex_log("s1", 2, self.cwd))
        events = self.collect()
        self.assertTrue(all("tool_calls" in e and "tool_failures" in e for e in events))
        stripped = {k: v for k, v in events[0].items() if k not in ("tool_calls", "tool_calls_total", "tool_failures", "fingerprint")}
        self.assertNotEqual(collector.fingerprint(stripped), events[0]["fingerprint"])

    def test_state_expires_old_entries(self):
        self.log.write_text(codex_log("s1", 1, self.cwd))
        self.collect(now=1_000_000)
        os.utime(self.log, (1_000_000 + 40 * 86400, 1_000_000 + 40 * 86400))
        self.collect(now=1_000_000 + 40 * 86400)
        self.assertTrue(all(v[1] >= 1_000_000 + 39 * 86400 for v in self.state["orca_calls"].values()))


class DispatcherSyncTests(unittest.TestCase):
    """init-telemetry.sh re-creates the dispatcher on re-runs, so both copies must agree."""

    def function_text(self, path: Path) -> str:
        text = path.read_text()
        match = re.search(r"CREATE OR REPLACE FUNCTION observability\.ingest_hermes_telemetry\(.*?\n\$\$;\n", text, re.S)
        self.assertIsNotNone(match, path)
        return match.group(0)

    def test_latest_migration_matches_init_script_dispatcher(self):
        sql = HERE.parents[2] / "homelab" / "timescaledb"
        defining = sorted(m for m in (sql / "migrations").glob("*.sql")
                          if "CREATE OR REPLACE FUNCTION observability.ingest_hermes_telemetry(" in m.read_text())
        self.assertGreaterEqual(len(defining), 2)  # 005 (call events) and 006 (task inputs)
        init = self.function_text(sql / "init-telemetry.sh")
        self.assertEqual(init, self.function_text(defining[-1]), defining[-1].name)
        for kind in ("orca_call_event", "orca_task_manifest", "orca_task_result"):
            self.assertIn(f"WHEN '{kind}'", init)

    def test_n8n_workflow_accepts_the_array_with_matching_kind(self):
        wf = json.loads((HERE.parents[2] / "homelab" / "n8n" / "workflows" / "hermes-telemetry-ingest.json").read_text())
        wf = wf[0] if isinstance(wf, list) else wf
        code = next(n for n in wf["nodes"] if n["type"].endswith(".code"))["parameters"]["jsCode"]
        self.assertIn("array: 'orca_call_events', kind: 'orca_call_event'", code)
        self.assertIn("array: 'orca_task_manifests', kind: 'orca_task_manifest'", code)
        self.assertIn("array: 'orca_task_results', kind: 'orca_task_result'", code)
        self.assertIn("[2, 3].includes(body.schema_version)", code)  # no schema bump needed


if __name__ == "__main__":
    unittest.main()
