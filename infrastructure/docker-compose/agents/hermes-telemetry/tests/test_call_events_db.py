"""Integration test against a disposable TimescaleDB container (never production).

Set ORCA_TEST_DB_CONTAINER (e.g. orca-obs-test-db) to enable; skipped otherwise.
"""
from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).parent
CONTAINER = os.getenv("ORCA_TEST_DB_CONTAINER")
MIGRATIONS = HERE.parents[2] / "homelab" / "timescaledb" / "migrations"
MIGRATION = MIGRATIONS / "001-orca-call-events.sql"
MIGRATION_TOOLS = MIGRATIONS / "008-orca-tool-calls.sql"

SPEC = importlib.util.spec_from_file_location("orca_calls", HERE.parent / "orca_calls.py")
orca_calls = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = orca_calls
SPEC.loader.exec_module(orca_calls)


def psql(sql: str, *, user="postgres", password=None, variables=(), stdin=None):
    cmd = ["docker", "exec", "-i", "-e", f"PGPASSWORD={password or os.getenv('ORCA_TEST_DB_PASSWORD', 'orca-obs-test')}",
           CONTAINER, "psql", "-v", "ON_ERROR_STOP=1", "-At", "-U", user, "-d", "observability"]
    for name, value in variables:
        cmd += ["-v", f"{name}={value}"]
    if stdin is None:
        stdin = sql  # stdin (not -c) so psql expands :'var' interpolation
    return subprocess.run(cmd, input=stdin, capture_output=True, text=True)


def sample_event(request_id="r1", status="final", revision=1, **overrides):
    usage = {"input_tokens": 100, "cached_input_tokens": 60, "cache_write_input_tokens": 0,
             "output_tokens": 5, "reasoning_output_tokens": 1, "total_tokens": 105}
    lines = [json.dumps({"type": "session_meta", "payload": {"id": "s", "cwd": "/w/a"}}),
             json.dumps({"type": "turn_context", "payload": {"cwd": "/w/a", "model": "gpt-x"}}),
             json.dumps({"type": "token_usage_record", "timestamp": "2026-10-09T01:00:00Z",
                         "payload": {"response_id": request_id, "session_id": "s", "usage": usage}})]
    event = orca_calls.extract_call_events("codex", lines, "test-ns", lambda c: True)[0][0]
    event.update(event_status=status, revision=revision, **overrides)
    return event


def ingest(event):
    result = psql("SELECT observability.ingest_orca_call_event(:'ev'::jsonb, 'test-collector', now())",
                  variables=[("ev", json.dumps(event))])
    assert result.returncode == 0, result.stderr
    return result.stdout.strip() == "t"


def scalar(sql):
    result = psql(sql)
    assert result.returncode == 0, result.stderr
    return result.stdout.strip()


@unittest.skipUnless(CONTAINER, "ORCA_TEST_DB_CONTAINER not set")
class CallEventDbTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        for path in (MIGRATION, MIGRATION_TOOLS):
            for _ in range(2):  # migrations must be re-runnable
                result = psql("", variables=[("app_user", "app_test")], stdin=path.read_text())
                assert result.returncode == 0, result.stderr

    def setUp(self):
        scalar("DELETE FROM observability.orca_call_events")

    def test_replay_is_idempotent(self):
        event = sample_event()
        self.assertTrue(ingest(event))
        self.assertFalse(ingest(event))
        self.assertEqual(scalar("SELECT count(*) FROM observability.orca_call_events"), "1")

    def test_final_is_not_replaced_by_late_partial(self):
        ingest(sample_event(status="partial", revision=1))
        self.assertTrue(ingest(sample_event(status="final", revision=2)))
        self.assertFalse(ingest(sample_event(status="partial", revision=9)))
        self.assertEqual(scalar("SELECT event_status FROM observability.orca_call_events"), "final")

    def test_correction_needs_higher_revision(self):
        ingest(sample_event(revision=5))
        self.assertFalse(ingest(sample_event(revision=5, output_tokens=999, processed_total_tokens=999)))
        self.assertEqual(scalar("SELECT output_tokens FROM observability.orca_call_events"), "5")
        self.assertTrue(ingest(sample_event(revision=6, output_tokens=7, processed_total_tokens=107)))
        self.assertEqual(scalar("SELECT output_tokens FROM observability.orca_call_events"), "7")

    def test_unknown_stays_null_not_zero(self):
        ingest(sample_event(non_cache_tokens=None, unknown_fields={"non_cache_tokens": "missing_in_source"}))
        self.assertEqual(scalar("SELECT non_cache_tokens IS NULL FROM observability.orca_call_events"), "t")
        self.assertEqual(scalar("SELECT unknown_fields->>'non_cache_tokens' FROM observability.orca_call_events"),
                         "missing_in_source")

    def test_payload_has_no_path_or_text_columns(self):
        ingest(sample_event())
        dump = scalar("SELECT row_to_json(e)::text FROM observability.orca_call_events e")
        self.assertNotIn("/w/a", dump)

    def test_invalid_payload_and_negative_tokens_are_rejected(self):
        bad = sample_event()
        bad["event_key"] = ""
        self.assertIn("invalid Orca call event payload", psql(
            "SELECT observability.ingest_orca_call_event(:'ev'::jsonb, 'c', now())",
            variables=[("ev", json.dumps(bad))]).stderr)
        negative = sample_event(output_tokens=-1)
        self.assertNotEqual(psql("SELECT observability.ingest_orca_call_event(:'ev'::jsonb, 'c', now())",
                                 variables=[("ev", json.dumps(negative))]).returncode, 0)

    def test_tool_fields_are_stored_and_unknown_stays_null(self):
        known = sample_event("known", tool_calls={"Bash": 2, "mcp:x": 1}, tool_calls_total=3, tool_failures=1)
        unknown = sample_event("unknown", tool_calls=None, tool_calls_total=None, tool_failures=None)
        none_used = sample_event("none", tool_calls={}, tool_calls_total=0, tool_failures=0)
        for event in (known, unknown, none_used):
            ingest(event)
        rows = scalar("SELECT string_agg(coalesce(tool_calls::text,'NULL')||'/'||coalesce(tool_calls_total::text,'NULL')||'/'||coalesce(tool_failures::text,'NULL'), ' ; ' ORDER BY tool_calls_total NULLS FIRST) FROM observability.orca_call_events")
        self.assertEqual(rows, 'NULL/NULL/NULL ; {}/0/0 ; {"Bash": 2, "mcp:x": 1}/3/1')

    def test_backfill_fills_missing_tool_info_at_the_same_revision(self):
        ingest(sample_event(revision=5, tool_calls=None, tool_calls_total=None, tool_failures=None))
        self.assertTrue(ingest(sample_event(revision=5, tool_calls={"exec": 4}, tool_calls_total=4, tool_failures=None)))
        self.assertEqual(scalar("SELECT tool_calls_total FROM observability.orca_call_events"), "4")
        self.assertFalse(ingest(sample_event(revision=5, tool_calls={"exec": 9}, tool_calls_total=9, tool_failures=None)))  # already known
        self.assertEqual(scalar("SELECT tool_calls_total FROM observability.orca_call_events"), "4")

    def test_a_partial_update_never_overwrites_a_final_row_even_when_backfilling(self):
        ingest(sample_event(status="final", revision=5, tool_calls=None, tool_calls_total=None, tool_failures=None))
        self.assertFalse(ingest(sample_event(status="partial", revision=5, tool_calls={"Bash": 1}, tool_calls_total=1, tool_failures=0)))
        self.assertEqual(scalar("SELECT event_status||'/'||coalesce(tool_calls_total::text,'NULL') FROM observability.orca_call_events"), "final/NULL")

    def test_a_later_tool_result_updates_the_failure_count_with_a_higher_revision(self):
        ingest(sample_event(revision=5, tool_calls={"Bash": 1}, tool_calls_total=1, tool_failures=0))
        self.assertTrue(ingest(sample_event(revision=9, tool_calls={"Bash": 1}, tool_calls_total=1, tool_failures=1)))
        self.assertEqual(scalar("SELECT tool_failures FROM observability.orca_call_events"), "1")

    def test_malformed_tool_payloads_are_rejected(self):
        for bad in ({"tool_calls": ["Bash"]}, {"tool_calls": {"x" * 5000: 1}}):
            result = psql("SELECT observability.ingest_orca_call_event(:'ev'::jsonb, 'c', now())",
                          variables=[("ev", json.dumps(sample_event(**bad)))])
            self.assertIn("invalid Orca call event payload", result.stderr)
        negative = psql("SELECT observability.ingest_orca_call_event(:'ev'::jsonb, 'c', now())",
                        variables=[("ev", json.dumps(sample_event(tool_calls={}, tool_calls_total=-1)))])
        self.assertNotEqual(negative.returncode, 0)

    def test_app_user_can_only_use_function(self):
        ok = psql("SELECT observability.ingest_orca_call_event(:'ev'::jsonb, 'c', now())",
                  user="app_test", password="app-test-pw", variables=[("ev", json.dumps(sample_event()))])
        self.assertEqual(ok.returncode, 0, ok.stderr)
        direct = psql("DELETE FROM observability.orca_call_events", user="app_test", password="app-test-pw")
        self.assertNotEqual(direct.returncode, 0)


if __name__ == "__main__":
    unittest.main()
