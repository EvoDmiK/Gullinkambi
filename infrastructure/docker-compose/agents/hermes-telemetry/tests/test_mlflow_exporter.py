"""Integration tests: real disposable TimescaleDB + MLflow, with client fault injection.

Enable with ORCA_TEST_PG_DSN and ORCA_TEST_MLFLOW_URI (never point at production).
Requires `psycopg` and `mlflow`; skipped otherwise.
"""
from __future__ import annotations

import importlib.util
import os
import sys
import unittest
import uuid
from pathlib import Path

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE.parent))

DSN = os.getenv("ORCA_TEST_PG_DSN")
URI = os.getenv("ORCA_TEST_MLFLOW_URI")
try:
    import psycopg
    from mlflow import MlflowClient
    HAVE_DEPS = True
except ImportError:
    HAVE_DEPS = False

import orca_attribution  # noqa: E402
import orca_calls  # noqa: E402
import orca_snapshot  # noqa: E402
import mlflow_exporter  # noqa: E402


def call(agent="claude", **usage):
    base = {"input_tokens": 1, "cache_read_input_tokens": 90, "cache_creation_input_tokens": 0, "output_tokens": 9}
    row = orca_calls.normalize_call(agent, {**base, **usage})
    return {**row, "source_kind": "raw_call", "event_status": "final"}


RESULT = orca_attribution.validate_result({
    "schema_version": 1, "event_id": "res-1", "task_id": "t", "attempt_id": "a", "result_revision": 1,
    "occurred_at": "2026-10-09T02:00:00Z", "producer_id": "verifier-1", "producer_type": "verifier",
    "evidence_kind": "real", "reported_status": "completed", "verification_state": "evidence_verified",
    "test": {"test_id": "unit", "exit_code": 0, "passed": 5, "failed": 0, "skipped": 0},
    "artifact_handle": "sum-1", "artifact_sha256": "0" * 64})


class FaultyClient:
    """Delegates to a real MlflowClient; named methods can be made to fail."""

    def __init__(self, real):
        self.real, self.fail, self.lose_create_response, self.calls = real, set(), False, []

    def __getattr__(self, name):
        target = getattr(self.real, name)
        if not callable(target):
            return target

        def wrapper(*args, **kwargs):
            self.calls.append(name)
            if name in self.fail:
                raise ConnectionError("token=SECRET-MARKER connection refused")
            result = target(*args, **kwargs)
            if name == "create_run" and self.lose_create_response:
                raise TimeoutError("response lost")
            return result
        return wrapper


@unittest.skipUnless(DSN and URI and HAVE_DEPS, "needs ORCA_TEST_PG_DSN, ORCA_TEST_MLFLOW_URI, psycopg, mlflow")
class ExporterIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.conn = psycopg.connect(DSN, autocommit=True)
        self.real = MlflowClient(URI)
        self.client = FaultyClient(self.real)
        self.experiment = f"orca-test-{uuid.uuid4().hex[:8]}"
        self.task, self.attempt = f"t-{uuid.uuid4().hex[:6]}", "a1"
        self.exporter = mlflow_exporter.Exporter(self.conn, self.client, self.experiment, reconcile_limit=2)

    def tearDown(self):
        exp = self.real.get_experiment_by_name(self.experiment)
        for run in (self.real.search_runs([exp.experiment_id]) if exp else []):
            self.real.delete_run(run.info.run_id)
        self.conn.execute("DELETE FROM observability.orca_mlflow_outbox WHERE task_id=%s", (self.task,))
        self.conn.execute("DELETE FROM observability.orca_mlflow_runs WHERE task_id=%s", (self.task,))
        self.conn.execute("DELETE FROM observability.orca_attempt_snapshots WHERE task_id=%s", (self.task,))
        self.conn.close()

    # helpers
    def record(self, calls, result=RESULT):
        snap = orca_snapshot.build_snapshot(self.task, self.attempt, "proj-x", calls, result, {"verifier-1"})
        import json
        return self.conn.execute("SELECT observability.record_orca_attempt_snapshot(%s::jsonb)",
                                 (json.dumps(snap),)).fetchone()[0]

    def runs(self):
        exp = self.real.get_experiment_by_name(self.experiment)
        return self.real.search_runs([exp.experiment_id]) if exp else []

    def row(self, sql):
        return self.conn.execute(sql, (self.task,)).fetchall()

    def force_due(self):
        self.conn.execute("UPDATE observability.orca_mlflow_outbox SET next_attempt_at=now() WHERE task_id=%s", (self.task,))

    # tests
    def test_happy_path_publishes_verified_run(self):
        self.assertEqual(self.record([call(), call()]), 1)
        self.assertEqual(self.exporter.run_once()["sent"], 1)
        (run,) = self.runs()
        self.assertEqual(run.data.tags["orca.committed_revision"], "1")
        self.assertEqual(run.data.tags["orca.verification_state"], "evidence_verified")
        self.assertEqual(run.data.metrics["request_count"], 2.0)
        self.assertEqual(run.data.metrics["processed_total_tokens"], 200.0)
        self.assertEqual(run.data.metrics["test_passed"], 5.0)
        self.assertEqual(run.info.status, "FINISHED")
        self.assertEqual(self.row("SELECT committed_snapshot_revision FROM observability.orca_mlflow_runs WHERE task_id=%s")[0][0], 1)
        self.assertEqual(self.row("SELECT state FROM observability.orca_mlflow_outbox WHERE task_id=%s")[0][0], "sent")

    def test_replay_and_restart_do_not_duplicate_runs(self):
        self.record([call()])
        self.exporter.run_once()
        self.assertIsNone(self.record([call()]))  # identical snapshot → no new revision
        self.assertEqual(self.exporter.run_once()["sent"], 0)
        restarted = mlflow_exporter.Exporter(self.conn, self.client, self.experiment)
        self.assertEqual(restarted.run_once()["sent"], 0)
        self.assertEqual(len(self.runs()), 1)

    def test_late_revision_updates_same_run_after_finish(self):
        self.record([call()])
        self.exporter.run_once()
        self.assertEqual(self.record([call(), call(output_tokens=11)]), 2)
        self.assertEqual(self.exporter.run_once()["sent"], 1)
        (run,) = self.runs()
        self.assertEqual(run.data.metrics["request_count"], 2.0)
        steps = sorted(m.step for m in self.real.get_metric_history(run.info.run_id, "request_count"))
        self.assertEqual(steps, [1, 2])  # history kept; readers use the committed revision, not the sum
        self.assertEqual(run.data.tags["orca.committed_revision"], "2")

    def test_unknown_metric_is_absent_not_zero(self):
        partial = orca_calls.normalize_call("claude", {"input_tokens": 1, "output_tokens": 2})
        self.record([call(), {**partial, "source_kind": "raw_call", "event_status": "final"}])
        self.exporter.run_once()
        (run,) = self.runs()
        self.assertNotIn("processed_total_tokens", run.data.metrics)
        self.assertIn("processed_total_tokens", run.data.tags["orca.unavailable_metrics"])
        self.assertEqual(run.data.metrics["request_count"], 2.0)

    def test_mlflow_outage_keeps_db_and_recovers_to_same_run(self):
        self.record([call()])
        self.client.fail = {"get_experiment_by_name"}
        counts = self.exporter.run_once()
        self.assertEqual(counts["retry"], 1)
        state, attempts, code = self.row("SELECT state, attempt_count, last_error_code FROM observability.orca_mlflow_outbox WHERE task_id=%s")[0]
        self.assertEqual((state, attempts, code), ("pending", 1, "ConnectionError"))  # no secret text stored
        self.assertEqual(self.exporter.run_once()["sent"], 0)  # backoff not elapsed
        self.assertEqual(self.row("SELECT count(*) FROM observability.orca_attempt_snapshots WHERE task_id=%s")[0][0], 1)
        self.client.fail = set()
        self.force_due()
        self.assertEqual(self.exporter.run_once()["sent"], 1)
        self.assertEqual(len(self.runs()), 1)

    def test_lost_create_response_is_reconciled_without_duplicate(self):
        self.record([call()])
        self.client.lose_create_response = True
        counts = self.exporter.run_once()
        self.assertEqual(counts["sent"], 1)
        self.assertEqual(len(self.runs()), 1)
        self.assertEqual(self.client.calls.count("create_run"), 1)
        self.assertEqual(self.row("SELECT create_outcome FROM observability.orca_mlflow_runs WHERE task_id=%s")[0][0], "created")

    def test_uncertain_create_with_no_candidate_blocks_instead_of_recreating(self):
        self.record([call()])
        self.client.fail = {"create_run"}
        self.exporter.run_once()
        self.force_due()
        counts = self.exporter.run_once()
        self.assertEqual(counts["dead"], 1)
        self.assertEqual(self.client.calls.count("create_run"), 1)
        self.assertEqual(self.runs(), [])
        self.assertEqual(self.row("SELECT create_outcome FROM observability.orca_mlflow_runs WHERE task_id=%s")[0][0], "uncertain")
        self.assertEqual(self.row("SELECT last_error_code FROM observability.orca_mlflow_outbox WHERE task_id=%s")[0][0], "create_uncertain_unresolved")

    def test_duplicate_candidates_become_conflict(self):
        self.record([call()])
        exp_id = self.exporter.experiment_id()
        key = mlflow_exporter.correlation_key(self.experiment, self.task, self.attempt)
        for _ in range(2):
            self.real.create_run(exp_id, tags={mlflow_exporter.CORRELATION_TAG: key})
        self.conn.execute("""INSERT INTO observability.orca_mlflow_runs (task_id, attempt_id, experiment, correlation_key, create_outcome)
                             VALUES (%s, %s, %s, %s, 'uncertain')""", (self.task, self.attempt, self.experiment, key))
        self.assertEqual(self.exporter.run_once()["dead"], 1)
        self.assertEqual(self.row("SELECT create_outcome FROM observability.orca_mlflow_runs WHERE task_id=%s")[0][0], "conflict")

    def test_only_latest_pending_revision_is_published_and_pointer_never_regresses(self):
        self.record([call()])
        self.record([call(), call()])
        self.assertEqual(self.exporter.run_once()["sent"], 1)
        states = dict(self.conn.execute("SELECT revision, state FROM observability.orca_mlflow_outbox WHERE task_id=%s", (self.task,)).fetchall())
        self.assertEqual(states, {1: "superseded", 2: "sent"})
        (run,) = self.runs()
        self.assertEqual(run.data.metrics["request_count"], 2.0)
        self.conn.execute("""INSERT INTO observability.orca_mlflow_outbox (task_id, attempt_id, revision)
                             VALUES (%s, %s, 1) ON CONFLICT (task_id, attempt_id, revision) DO UPDATE SET state='pending', next_attempt_at=now()""",
                          (self.task, self.attempt))
        self.assertEqual(self.exporter.run_once()["stale"], 1)
        self.assertEqual(self.row("SELECT committed_snapshot_revision FROM observability.orca_mlflow_runs WHERE task_id=%s")[0][0], 2)
        self.assertEqual(self.runs()[0].data.tags["orca.committed_revision"], "2")

    def test_second_exporter_is_locked_out(self):
        self.record([call()])
        other = psycopg.connect(DSN, autocommit=True)
        try:
            other.execute("SELECT pg_advisory_lock(%s)", (mlflow_exporter.ADVISORY_LOCK_KEY,))
            self.assertEqual(self.exporter.run_once()["locked"], 1)
            self.assertEqual(self.runs(), [])
        finally:
            other.close()

    def test_no_secret_or_path_markers_reach_mlflow(self):
        self.record([call()])
        self.exporter.run_once()
        (run,) = self.runs()
        blob = repr(run.data.tags) + repr(run.data.metrics) + repr(run.info)
        for marker in ("SECRET-MARKER", "/Users/", "/Volumes/"):
            self.assertNotIn(marker, blob)


if __name__ == "__main__":
    unittest.main()
