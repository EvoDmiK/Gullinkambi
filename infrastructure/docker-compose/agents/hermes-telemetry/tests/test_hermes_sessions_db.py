"""Integration test for migrations 009-011 against a disposable TimescaleDB container (never production).

Set ORCA_TEST_DB_CONTAINER to a container initialised with homelab/timescaledb/init-telemetry.sh and the roles
`app` (TIMESCALE_APP_USER) and `bak`; skipped otherwise. The migrations are applied by the test itself.
"""
from __future__ import annotations

import json
import os
import subprocess
import unittest
from pathlib import Path

CONTAINER = os.getenv("ORCA_TEST_DB_CONTAINER")
MIGRATIONS = Path(__file__).resolve().parents[3] / "homelab" / "timescaledb" / "migrations"
APPLY = ("009-hermes-sessions.sql", "010-hermes-tool-outcome-refresh.sql", "011-hermes-tool-log-events.sql")


def psql(sql: str, user="postgres", variables=(("app_user", "app"),)):
    cmd = ["docker", "exec", "-i", CONTAINER, "psql", "-v", "ON_ERROR_STOP=1", "-At", "-U", user, "-d", "observability"]
    for name, value in variables:
        cmd += ["-v", f"{name}={value}"]
    return subprocess.run(cmd, input=sql, capture_output=True, text=True)


def scalar(sql: str, **kw) -> str:
    result = psql(sql, **kw)
    assert result.returncode == 0, result.stderr
    return result.stdout.strip()


def dispatch(kind: str, item: dict) -> bool:
    return scalar(f"SELECT observability.ingest_hermes_telemetry('{kind}', $j${json.dumps(item)}$j$::jsonb, 'test', now())") == "t"


def sample(session, started, sampled, tokens_in, tokens_out=0, cache=0, ended=None, expiry=False, last=None):
    return {"event_key": f"hs:{session}:{sampled}:{tokens_in}", "schema_version": 1, "profile": "p", "session_hash": session,
            "source": "discord", "model": "m", "provider": "x", "started_at": started, "last_activity_at": last or sampled,
            "ended_at": ended, "expiry_finalized": expiry, "sampled_at": sampled, "api_call_count": 1,
            "input_tokens": tokens_in, "output_tokens": tokens_out, "cache_read_tokens": cache, "fingerprint": "f"}


@unittest.skipUnless(CONTAINER, "ORCA_TEST_DB_CONTAINER not set")
class HermesSessionsDbTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        for name in APPLY:
            result = psql((MIGRATIONS / name).read_text())
            assert result.returncode == 0, f"{name}: {result.stderr}"
        psql("TRUNCATE observability.hermes_session_samples, observability.hermes_tool_events, observability.hermes_tool_log_events")

    def test_samples_are_append_only_and_deltas_skip_prehistory(self):
        s1 = sample("new", "2026-10-10T00:00:00Z", "2026-10-10T00:01:00Z", 100)
        self.assertTrue(dispatch("hermes_session_sample", s1))
        self.assertFalse(dispatch("hermes_session_sample", s1))  # replay
        dispatch("hermes_session_sample", sample("new", "2026-10-10T00:00:00Z", "2026-10-10T00:02:00Z", 250, cache=40))
        dispatch("hermes_session_sample", sample("old", "2026-10-01T00:00:00Z", "2026-10-10T00:01:00Z", 5000))
        dispatch("hermes_session_sample", sample("old", "2026-10-01T00:00:00Z", "2026-10-10T00:03:00Z", 5400, expiry=True))
        rows = scalar("SELECT session_hash || ':' || known || ':' || COALESCE(input_tokens::text, 'null') FROM observability.hermes_usage_deltas ORDER BY session_hash, sampled_at")
        self.assertEqual(rows.split("\n"), ["new:true:100", "new:true:150", "old:false:null", "old:true:400"])
        states = scalar("SELECT string_agg(session_hash || '=' || state, ',' ORDER BY session_hash) FROM observability.hermes_sessions_current")
        self.assertEqual(states, "new=idle,old=ended")

    def test_tool_outcome_refresh_only_upgrades_unknown(self):
        base = {"event_key": "ht:1", "schema_version": 1, "profile": "p", "session_hash": "s", "occurred_at": "2026-10-10T00:00:00Z", "tool": "t"}
        dispatch("hermes_tool_event", {**base, "outcome": "unknown"})
        self.assertTrue(dispatch("hermes_tool_event", {**base, "outcome": "ok"}))
        self.assertFalse(dispatch("hermes_tool_event", {**base, "outcome": "error"}))  # a known outcome is final
        self.assertEqual(scalar("SELECT outcome FROM observability.hermes_tool_events WHERE event_key = 'ht:1'"), "ok")

    def test_tool_log_events_validate_and_deduplicate(self):
        item = {"event_key": "hl:1", "schema_version": 1, "profile": "p", "session_hash": "s", "occurred_at": "2026-10-10T00:00:00Z",
                "tool": "mcp:x", "status": "error", "duration_ms": 1200}
        self.assertTrue(dispatch("hermes_tool_log_event", item))
        self.assertFalse(dispatch("hermes_tool_log_event", item))
        self.assertNotEqual(psql(f"SELECT observability.ingest_hermes_telemetry('hermes_tool_log_event', $j${json.dumps({**item, 'event_key': 'hl:2', 'status': 'weird'})}$j$::jsonb, 'test', now())").returncode, 0)

    def test_app_role_can_only_use_the_dispatcher(self):
        result = psql("INSERT INTO observability.hermes_tool_events VALUES ('x',1,'p','s',now(),'t','ok','c',now())", user="app")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("permission denied", result.stderr)


if __name__ == "__main__":
    unittest.main()
