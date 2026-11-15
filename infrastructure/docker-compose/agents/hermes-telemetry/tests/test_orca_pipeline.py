"""End-to-end (disposable DB + MLflow): inputs → attribution → snapshot → exporter → MLflow."""
from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
import uuid
from pathlib import Path

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE.parent))

DSN, URI = os.getenv("ORCA_TEST_PG_DSN"), os.getenv("ORCA_TEST_MLFLOW_URI")
try:
    import psycopg
    from mlflow import MlflowClient
    HAVE_DEPS = True
except ImportError:
    HAVE_DEPS = False

import mlflow_exporter  # noqa: E402
import orca_calls  # noqa: E402
import orca_pipeline  # noqa: E402

USAGE = {"input_tokens": 100, "cached_input_tokens": 60, "cache_write_input_tokens": 0,
         "output_tokens": 5, "reasoning_output_tokens": 1, "total_tokens": 105}


def codex_events(session, stamps):
    lines = [json.dumps({"type": "session_meta", "payload": {"id": session, "cwd": "/w/a"}}),
             json.dumps({"type": "turn_context", "payload": {"cwd": "/w/a", "model": "gpt-x"}})]
    for i, stamp in enumerate(stamps):
        lines.append(json.dumps({"type": "token_usage_record", "timestamp": stamp, "payload": {
            "response_id": f"{session}-r{i}", "session_id": session, "usage": USAGE}}))
    return orca_calls.extract_call_events("codex", lines, "test-ns", lambda c: True)[0]


def manifest(task, session, end, revision=1, attempt="a1"):
    return {"schema_version": 1, "producer_id": "manual", "task_id": task, "attempt_id": attempt,
            "project_key": "proj-x", "session_id": session, "revision": revision,
            "window": {"start": "2026-10-09T01:00:00Z", "end": end}}


def result(task, attempt="a1", kind="real", producer="verifier-1"):
    return {"schema_version": 1, "event_id": f"res-{task}", "task_id": task, "attempt_id": attempt,
            "result_revision": 1, "occurred_at": "2026-10-09T02:00:00Z", "producer_id": producer,
            "producer_type": "verifier", "evidence_kind": kind, "reported_status": "completed",
            "verification_state": "evidence_verified",
            "test": {"test_id": "unit", "exit_code": 0, "passed": 3, "failed": 0, "skipped": 0}}


@unittest.skipUnless(DSN and URI and HAVE_DEPS, "needs ORCA_TEST_PG_DSN, ORCA_TEST_MLFLOW_URI, psycopg, mlflow")
class PipelineEndToEndTests(unittest.TestCase):
    def setUp(self):
        self.conn = psycopg.connect(DSN, autocommit=True)
        self.real = MlflowClient(URI)
        self.suffix = uuid.uuid4().hex[:6]
        self.session, self.task = f"sess-{self.suffix}", f"task-{self.suffix}"
        self.experiment = f"orca-e2e-{self.suffix}"
        self.exporter = mlflow_exporter.Exporter(self.conn, self.real, self.experiment)
        self.events = codex_events(self.session, ["2026-10-09T01:10:00Z", "2026-10-09T01:20:00Z", "2026-10-09T02:40:00Z"])
        for event in self.events:
            self.conn.execute("SELECT observability.ingest_orca_call_event(%s::jsonb, 'e2e', now())", (json.dumps(event),))

    def tearDown(self):
        sh = orca_calls._hash(self.session)
        self.conn.execute("DELETE FROM observability.orca_mlflow_outbox WHERE task_id LIKE %s", (f"%{self.suffix}",))
        self.conn.execute("DELETE FROM observability.orca_mlflow_runs WHERE task_id LIKE %s", (f"%{self.suffix}",))
        self.conn.execute("DELETE FROM observability.orca_attempt_snapshots WHERE task_id LIKE %s", (f"%{self.suffix}",))
        self.conn.execute("DELETE FROM observability.orca_call_events WHERE session_id_hash = %s", (sh,))
        exp = self.real.get_experiment_by_name(self.experiment)
        for run in (self.real.search_runs([exp.experiment_id]) if exp else []):
            self.real.delete_run(run.info.run_id)
        self.conn.close()

    def write_inputs(self, manifests=(), results=(), extra_manifests=()):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        for kind, docs in (("manifests", [*manifests, *extra_manifests]), ("results", results)):
            (root / kind).mkdir()
            for i, doc in enumerate(docs):
                (root / kind / f"{i}.json").write_text(json.dumps(doc))
        return root

    def run_all(self, root, allow_fixture=False):
        counts = orca_pipeline.run_pipeline(self.conn, orca_pipeline.load_inputs(root, allow_fixture), {"verifier-1"})
        return counts, self.exporter.run_once()

    def run_metrics(self):
        exp = self.real.get_experiment_by_name(self.experiment)
        runs = self.real.search_runs([exp.experiment_id]) if exp else []
        return runs

    def per_call(self):
        return sum(e["processed_total_tokens"] for e in self.events)

    def test_window_attribution_publishes_verified_run_and_is_rerunnable(self):
        root = self.write_inputs([manifest(self.task, self.session, "2026-10-09T02:00:00Z")], [result(self.task)])
        counts, exported = self.run_all(root)
        self.assertEqual((counts["snapshots_recorded"], exported["sent"]), (1, 1))
        (run,) = self.run_metrics()
        self.assertEqual(run.data.metrics["request_count"], 2.0)  # 02:40 call is outside [start, end)
        self.assertEqual(run.data.metrics["processed_total_tokens"], 210.0)
        self.assertEqual(run.data.tags["orca.verification_state"], "evidence_verified")
        statuses = dict(self.conn.execute(
            "SELECT attribution_status, count(*) FROM observability.orca_call_events WHERE session_id_hash=%s GROUP BY 1",
            (orca_calls._hash(self.session),)).fetchall())
        self.assertEqual(statuses, {"explicit": 2, "unassigned": 1})
        counts, exported = self.run_all(root)  # same inputs → nothing new
        self.assertEqual((counts["snapshots_recorded"], counts["attributed_changed"], exported["sent"]), (0, 0, 0))
        self.assertEqual(len(self.run_metrics()), 1)

    def test_manifest_correction_moves_calls_and_updates_same_run(self):
        self.run_all(self.write_inputs([manifest(self.task, self.session, "2026-10-09T02:00:00Z")], [result(self.task)]))
        counts, exported = self.run_all(self.write_inputs(
            [manifest(self.task, self.session, "2026-10-09T03:00:00Z", revision=2)], [result(self.task)]))
        self.assertEqual((counts["snapshots_recorded"], exported["sent"]), (1, 1))
        (run,) = self.run_metrics()
        self.assertEqual(run.data.metrics["request_count"], 3.0)
        self.assertEqual(run.data.metrics["processed_total_tokens"], float(self.per_call()))
        self.assertEqual(run.data.tags["orca.committed_revision"], "2")

    def test_overlapping_attempts_are_ambiguous_and_never_double_counted(self):
        other = manifest(self.task, self.session, "2026-10-09T02:00:00Z", attempt="a2")
        counts, _ = self.run_all(self.write_inputs(
            [manifest(self.task, self.session, "2026-10-09T02:00:00Z"), other], [result(self.task)]))
        self.assertEqual(counts["ambiguous"], 2)
        attributed = self.conn.execute(
            "SELECT count(*) FROM observability.orca_call_events WHERE session_id_hash=%s AND task_id IS NOT NULL",
            (orca_calls._hash(self.session),)).fetchone()[0]
        self.assertEqual(attributed, 0)
        for run in self.run_metrics():  # no observed calls: absent, not zero
            self.assertNotIn("request_count", run.data.metrics)
            self.assertEqual(run.data.tags["orca.usage_coverage"], "unknown")

    def test_fixture_and_unverified_producers_do_not_become_verified(self):
        inputs = orca_pipeline.load_inputs(
            self.write_inputs([manifest(self.task, self.session, "2026-10-09T02:00:00Z")], [result(self.task, kind="fixture")]))
        self.assertEqual(inputs["rejected"], {"fixture_result_not_allowed": 1})
        self.run_all(self.write_inputs([manifest(self.task, self.session, "2026-10-09T02:00:00Z")],
                                       [result(self.task, producer="unapproved")]))
        (run,) = self.run_metrics()
        self.assertEqual(run.data.tags["orca.verification_state"], "unverified")

    def test_invalid_inputs_are_counted_without_echoing_content(self):
        bad = {**manifest(self.task, self.session, "2026-10-09T02:00:00Z"), "title": "SECRET-MARKER /Users/x"}
        inputs = orca_pipeline.load_inputs(self.write_inputs([bad]))
        self.assertEqual(inputs["rejected"], {"manifest:unknown_field": 1})
        self.assertNotIn("SECRET-MARKER", repr(inputs))

    def test_no_raw_session_or_path_in_stored_rows(self):
        self.run_all(self.write_inputs([manifest(self.task, self.session, "2026-10-09T02:00:00Z")], [result(self.task)]))
        dump = "".join(r[0] for r in self.conn.execute(
            "SELECT row_to_json(e)::text FROM observability.orca_call_events e WHERE session_id_hash=%s",
            (orca_calls._hash(self.session),)).fetchall())
        self.assertNotIn(self.session, dump)
        self.assertNotIn("/w/a", dump)


EXPORTER_DSN = os.getenv("ORCA_TEST_EXPORTER_DSN")  # optional: the least-privilege role used in production


@unittest.skipUnless(DSN and URI and HAVE_DEPS, "needs ORCA_TEST_PG_DSN, ORCA_TEST_MLFLOW_URI, psycopg, mlflow")
class FromDbPipelineTests(unittest.TestCase):
    """orca_task.py -> dispatcher -> tables -> pipeline -> snapshots -> MLflow (no files involved)."""

    def setUp(self):
        self.conn = psycopg.connect(DSN, autocommit=True)
        self.real = MlflowClient(URI)
        self.suffix = uuid.uuid4().hex[:6]
        self.session, self.task = f"sess-{self.suffix}", f"task-{self.suffix}"
        self.experiment = f"orca-db-{self.suffix}"
        self.events = codex_events(self.session, ["2026-10-09T01:10:00Z", "2026-10-09T01:20:00Z", "2026-10-09T02:40:00Z"])
        for event in self.events:
            self.conn.execute("SELECT observability.ingest_orca_call_event(%s::jsonb, 'e2e', now())", (json.dumps(event),))

    def tearDown(self):
        sh = orca_calls._hash(self.session)
        like = f"%{self.suffix}"
        for table in ("orca_mlflow_outbox", "orca_mlflow_runs", "orca_attempt_snapshots", "orca_task_manifests", "orca_task_results"):
            self.conn.execute(f"DELETE FROM observability.{table} WHERE task_id LIKE %s", (like,))
        self.conn.execute("DELETE FROM observability.orca_call_events WHERE session_id_hash = %s", (sh,))
        exp = self.real.get_experiment_by_name(self.experiment)
        for run in (self.real.search_runs([exp.experiment_id]) if exp else []):
            self.real.delete_run(run.info.run_id)
        self.conn.close()

    def send(self, kind, item):
        return self.conn.execute("SELECT observability.ingest_hermes_telemetry(%s, %s::jsonb, 'orca-task-cli', now())",
                                 (kind, json.dumps(item))).fetchone()[0]

    def manifest(self, end, revision=1):
        return {"schema_version": 1, "producer_id": "orca-task-cli", "task_id": self.task, "attempt_id": "a1",
                "project_key": "proj-x", "session_id_hash": orca_calls._hash(self.session), "revision": revision,
                "window": {"start": "2026-10-09T01:00:00Z", "end": end}}

    def run_all(self, conn=None, approved=(), key=None):
        conn = conn or self.conn
        counts = orca_pipeline.run_pipeline(conn, orca_pipeline.load_inputs_from_db(conn, verifier_key=key), set(approved), key)
        exported = mlflow_exporter.Exporter(conn, self.real, self.experiment).run_once()
        return counts, exported

    def test_manifest_and_result_flow_from_the_webhook_tables_to_mlflow(self):
        self.assertTrue(self.send("orca_task_manifest", self.manifest("2026-10-09T02:00:00Z")))
        reported = result(self.task)
        reported.update(producer_type="agent", verification_state="unverified")
        self.assertTrue(self.send("orca_task_result", reported))
        counts, exported = self.run_all()
        self.assertEqual((counts["snapshots_recorded"], exported["sent"]), (1, 1))
        exp = self.real.get_experiment_by_name(self.experiment)
        (run,) = self.real.search_runs([exp.experiment_id])
        self.assertEqual(run.data.metrics["request_count"], 2.0)  # the 02:40 call is outside [start, end)
        self.assertEqual(run.data.tags["orca.reported_status"], "completed")
        self.assertEqual(run.data.tags["orca.verification_state"], "unverified")
        counts, exported = self.run_all()  # rerun changes nothing
        self.assertEqual((counts["snapshots_recorded"], counts["attributed_changed"], exported["sent"]), (0, 0, 0))

    def test_revisions_are_immutable_and_a_higher_revision_corrects_the_window(self):
        self.send("orca_task_manifest", self.manifest("2026-10-09T02:00:00Z"))
        self.assertFalse(self.send("orca_task_manifest", self.manifest("2026-10-09T03:00:00Z")))  # same revision: ignored
        self.run_all()
        self.assertTrue(self.send("orca_task_manifest", self.manifest("2026-10-09T03:00:00Z", revision=2)))
        counts, exported = self.run_all()
        self.assertEqual((counts["snapshots_recorded"], exported["sent"]), (1, 1))
        exp = self.real.get_experiment_by_name(self.experiment)
        (run,) = self.real.search_runs([exp.experiment_id])
        self.assertEqual(run.data.metrics["request_count"], 3.0)

    def test_only_an_approved_verifier_can_verify(self):
        self.send("orca_task_manifest", self.manifest("2026-10-09T02:00:00Z"))
        verified = result(self.task)  # verifier-1 / real / evidence_verified
        self.send("orca_task_result", verified)
        self.run_all(approved=())  # nobody approved -> downgraded
        exp = self.real.get_experiment_by_name(self.experiment)
        (run,) = self.real.search_runs([exp.experiment_id])
        self.assertEqual(run.data.tags["orca.verification_state"], "unverified")
        self.run_all(approved={"verifier-1"})
        (run,) = self.real.search_runs([exp.experiment_id])
        self.assertEqual(run.data.tags["orca.verification_state"], "evidence_verified")

    def test_signed_verifier_evidence_reaches_mlflow_and_survives_later_reports_and_forgeries(self):
        import orca_attribution
        key = "k" * 32
        self.send("orca_task_manifest", self.manifest("2026-10-09T02:00:00Z"))
        agent = {**result(self.task), "producer_id": "orca-task-cli", "producer_type": "agent", "verification_state": "unverified",
                 "event_id": f"res-{self.task}-1", "result_revision": 5, "reported_status": "completed"}
        self.send("orca_task_result", agent)
        evidence = {**result(self.task), "producer_id": "orca-verifier", "event_id": f"ver-{self.task}-1", "result_revision": 1}
        evidence["signature"] = orca_attribution.sign_result(evidence, key)
        self.send("orca_task_result", evidence)

        def run_tags():
            self.run_all(approved={"orca-verifier"}, key=key)
            exp = self.real.get_experiment_by_name(self.experiment)
            (run,) = self.real.search_runs([exp.experiment_id])
            return run.data.tags

        tags = run_tags()
        self.assertEqual((tags["orca.verification_state"], tags["orca.reported_status"]), ("evidence_verified", "completed"))
        # the agent reports again later (higher revision): the verification must not disappear
        self.send("orca_task_result", {**agent, "event_id": f"res-{self.task}-2", "result_revision": 6, "reported_status": "failed"})
        tags = run_tags()
        self.assertEqual((tags["orca.verification_state"], tags["orca.reported_status"]), ("evidence_verified", "failed"))
        # a forged, unsigned "verifier" result with a much higher revision changes nothing
        forged = {**evidence, "event_id": f"ver-{self.task}-99", "result_revision": 99, "verification_state": "failed"}
        forged.pop("signature")
        self.send("orca_task_result", forged)
        tags = run_tags()
        self.assertEqual(tags["orca.verification_state"], "evidence_verified")
        self.assertEqual(orca_pipeline.load_inputs_from_db(self.conn, verifier_key=key)["rejected"], {"result:verifier_signature_invalid": 1})

    def test_invalid_payloads_are_rejected_by_the_database_and_by_the_pipeline(self):
        for kind, bad in (("orca_task_manifest", {"task_id": "bad id!", "attempt_id": "a", "revision": 1, "producer_id": "p"}),
                          ("orca_task_result", {"event_id": "e", "task_id": self.task, "attempt_id": "a", "result_revision": "x", "producer_id": "p"}),
                          ("orca_task_manifest", {"task_id": self.task, "attempt_id": "a", "revision": 1, "producer_id": "p", "pad": "x" * 20000})):
            with self.assertRaises(psycopg.errors.RaiseException):
                self.send(kind, bad)
        # shape-valid for the DB but rejected (and only counted) by the pipeline's validators
        self.send("orca_task_manifest", {"task_id": self.task, "attempt_id": "a1", "revision": 1, "producer_id": "p", "project_key": "x", "note": "SECRET-MARKER"})
        loaded = orca_pipeline.load_inputs_from_db(self.conn)
        self.assertNotIn("SECRET-MARKER", repr(loaded["rejected"]))
        self.assertTrue(any(code.startswith("manifest:") for code in loaded["rejected"]))

    @unittest.skipUnless(EXPORTER_DSN, "needs ORCA_TEST_EXPORTER_DSN (the production exporter role)")
    def test_the_least_privilege_exporter_role_can_run_the_whole_flow(self):
        self.send("orca_task_manifest", self.manifest("2026-10-09T02:00:00Z"))
        with psycopg.connect(EXPORTER_DSN, autocommit=True) as exporter_conn:
            counts, exported = self.run_all(conn=exporter_conn)
            self.assertEqual((counts["snapshots_recorded"], exported["sent"]), (1, 1))
            for forbidden in ("DELETE FROM observability.orca_call_events", "UPDATE observability.orca_task_manifests SET revision = 9",
                              "SELECT count(*) FROM observability.hermes_runs"):
                with self.assertRaises(psycopg.errors.InsufficientPrivilege):
                    exporter_conn.execute(forbidden)


@unittest.skipUnless(DSN and URI and HAVE_DEPS, "needs ORCA_TEST_PG_DSN, ORCA_TEST_MLFLOW_URI, psycopg, mlflow")
class AutoSessionTests(unittest.TestCase):
    """Unregistered sessions become one automatic attempt (and MLflow Run) each.

    Automatic grouping looks at every recent call in the database, so these tests start from an
    empty one. They therefore refuse to run against anything but a local disposable database.
    """

    AUTO_TABLES = ("orca_mlflow_outbox", "orca_mlflow_runs", "orca_attempt_snapshots")

    def setUp(self):
        if not any(host in (DSN or "") for host in ("@127.0.0.1", "@localhost")):
            self.skipTest("auto-session tests wipe orca_call_events; local disposable DB only")
        self.conn = psycopg.connect(DSN, autocommit=True)
        self.real = MlflowClient(URI)
        self.suffix = uuid.uuid4().hex[:6]
        self.experiment = f"orca-auto-{self.suffix}"
        self.sessions = {}
        self.wipe()

    def wipe(self):
        self.conn.execute("DELETE FROM observability.orca_call_events")
        for table in self.AUTO_TABLES:
            self.conn.execute(f"DELETE FROM observability.{table} WHERE task_id LIKE 'auto-%%' OR task_id LIKE %s", (f"%{self.suffix}",))
        self.conn.execute("DELETE FROM observability.orca_task_manifests WHERE task_id LIKE %s", (f"%{self.suffix}",))

    def tearDown(self):
        if not hasattr(self, "conn"):
            return
        self.wipe()
        exp = self.real.get_experiment_by_name(self.experiment)
        for run in (self.real.search_runs([exp.experiment_id]) if exp else []):
            self.real.delete_run(run.info.run_id)
        self.conn.close()

    def add_session(self, name, stamps):
        session = f"{name}-{self.suffix}"
        self.sessions[name] = session
        for event in codex_events(session, stamps):
            self.conn.execute("SELECT observability.ingest_orca_call_event(%s::jsonb, 'auto', now())", (json.dumps(event),))
        return session

    def task(self, name):
        return f"auto-codex-{orca_calls._hash(self.sessions[name])[:8]}"

    def run_all(self, auto=True, throttle=0):
        counts = orca_pipeline.run_pipeline(self.conn, orca_pipeline.load_inputs_from_db(self.conn), set(), None,
                                            auto_sessions=auto, auto_throttle_seconds=throttle)
        exported = mlflow_exporter.Exporter(self.conn, self.real, self.experiment).run_once()
        return counts, exported

    def status(self, name):
        return dict(self.conn.execute(
            "SELECT coalesce(attribution_status,'-')||'/'||coalesce(attribution_method,'-'), count(*) FROM observability.orca_call_events "
            "WHERE session_id_hash = %s GROUP BY 1", (orca_calls._hash(self.sessions[name]),)).fetchall())

    def runs(self):
        exp = self.real.get_experiment_by_name(self.experiment)
        return {r.data.tags["orca.task_id"]: r for r in self.real.search_runs([exp.experiment_id])} if exp else {}

    def test_off_by_default(self):
        self.add_session("a", ["2026-10-09T01:10:00Z", "2026-10-09T01:20:00Z"])
        counts, exported = self.run_all(auto=False)
        self.assertEqual(self.status("a"), {"unassigned/-": 2})
        self.assertEqual((counts["auto_sessions"], exported["sent"]), (0, 0))

    def test_each_unregistered_session_becomes_its_own_run(self):
        self.add_session("a", ["2026-10-09T01:10:00Z", "2026-10-09T01:20:00Z"])
        self.add_session("b", ["2026-10-09T03:00:00Z"])
        counts, exported = self.run_all()
        self.assertEqual(self.status("a"), {"auto/auto_session": 2})
        runs = self.runs()
        run_a, run_b = runs[self.task("a")], runs[self.task("b")]
        self.assertEqual((run_a.data.metrics["request_count"], run_b.data.metrics["request_count"]), (2.0, 1.0))
        self.assertEqual(run_a.data.tags["orca.origin"], "auto")
        self.assertEqual(run_a.info.status, "FINISHED")  # no reported end, so it must not look permanently RUNNING
        self.assertEqual(run_a.data.tags["mlflow.runName"], f"{self.task('a')}/a1")  # readable, not random
        self.assertEqual((run_a.data.tags["orca.reported_status"], run_a.data.tags["orca.verification_state"]), ("unknown", "unknown"))
        again, exported = self.run_all()  # rerun changes nothing
        self.assertEqual((again["attributed_changed"], again["snapshots_recorded"], exported["sent"]), (0, 0, 0))

    def test_explicit_registration_wins_and_nothing_is_counted_twice(self):
        session = self.add_session("a", ["2026-10-09T01:10:00Z", "2026-10-09T01:20:00Z", "2026-10-09T02:40:00Z"])
        manifest = {"schema_version": 1, "producer_id": "orca-task-cli", "task_id": f"task-{self.suffix}", "attempt_id": "a1",
                    "project_key": "proj-x", "session_id_hash": orca_calls._hash(session), "revision": 1,
                    "window": {"start": "2026-10-09T01:00:00Z", "end": "2026-10-09T02:00:00Z"}}
        self.conn.execute("SELECT observability.ingest_hermes_telemetry('orca_task_manifest', %s::jsonb, 'x', now())", (json.dumps(manifest),))
        self.run_all()
        self.assertEqual(self.status("a"), {"explicit/session_window": 2, "auto/auto_session": 1})
        runs = self.runs()
        self.assertEqual(runs[f"task-{self.suffix}"].data.metrics["request_count"], 2.0)
        self.assertEqual(runs[self.task("a")].data.metrics["request_count"], 1.0)  # only the leftover call
        self.assertEqual(sum(r.data.metrics["request_count"] for r in runs.values()), 3.0)

    def test_registering_later_releases_calls_from_the_automatic_attempt(self):
        session = self.add_session("a", ["2026-10-09T01:10:00Z", "2026-10-09T01:20:00Z"])
        self.run_all()
        self.assertEqual(self.runs()[self.task("a")].data.metrics["request_count"], 2.0)
        manifest = {"schema_version": 1, "producer_id": "orca-task-cli", "task_id": f"task-{self.suffix}", "attempt_id": "a1",
                    "project_key": "proj-x", "session_id_hash": orca_calls._hash(session), "revision": 1,
                    "window": {"start": "2026-10-09T01:00:00Z", "end": "2026-10-09T01:15:00Z"}}
        self.conn.execute("SELECT observability.ingest_hermes_telemetry('orca_task_manifest', %s::jsonb, 'x', now())", (json.dumps(manifest),))
        self.run_all()
        runs = self.runs()
        self.assertEqual(runs[f"task-{self.suffix}"].data.metrics["request_count"], 1.0)
        self.assertEqual(runs[self.task("a")].data.metrics["request_count"], 1.0)

    def test_ambiguous_calls_are_not_grouped_automatically(self):
        session = self.add_session("a", ["2026-10-09T01:10:00Z"])
        for n in (1, 2):
            m = {"schema_version": 1, "producer_id": "p", "task_id": f"task{n}-{self.suffix}", "attempt_id": "a1", "project_key": "proj-x",
                 "session_id_hash": orca_calls._hash(session), "revision": 1,
                 "window": {"start": "2026-10-09T01:00:00Z", "end": "2026-10-09T02:00:00Z"}}
            self.conn.execute("SELECT observability.ingest_hermes_telemetry('orca_task_manifest', %s::jsonb, 'x', now())", (json.dumps(m),))
        self.run_all()
        self.assertEqual(self.status("a"), {"ambiguous/-": 1})
        self.assertNotIn(self.task("a"), self.runs())

    def test_busy_sessions_are_throttled_but_catch_up(self):
        self.add_session("a", ["2026-10-09T01:10:00Z"])
        self.run_all(throttle=600)
        for event in codex_events(self.sessions["a"], ["2026-10-09T01:10:00Z", "2026-10-09T01:30:00Z"]):
            self.conn.execute("SELECT observability.ingest_orca_call_event(%s::jsonb, 'auto', now())", (json.dumps(event),))
        counts, exported = self.run_all(throttle=600)
        self.assertGreaterEqual(counts["auto_throttled"], 1)
        self.assertEqual(self.runs()[self.task("a")].data.metrics["request_count"], 1.0)  # not republished yet
        counts, exported = self.run_all(throttle=0)  # window elapsed
        self.assertEqual(self.runs()[self.task("a")].data.metrics["request_count"], 2.0)

    def test_tool_use_reaches_the_run_as_metrics_and_the_artifact(self):
        self.add_session("a", ["2026-10-09T01:10:00Z", "2026-10-09T01:20:00Z"])
        self.conn.execute("""UPDATE observability.orca_call_events SET tool_calls = '{"exec": 3, "mcp:obsidian_vault": 1}'::jsonb,
                             tool_calls_total = 4, tool_failures = NULL WHERE session_id_hash = %s""", (orca_calls._hash(self.sessions["a"]),))
        self.run_all()
        run = self.runs()[self.task("a")]
        self.assertEqual((run.data.metrics["tool_calls_total"], run.data.metrics["tool.exec"], run.data.metrics["tool.mcp:obsidian_vault"]), (8.0, 6.0, 2.0))
        self.assertNotIn("tool_failures", run.data.metrics)  # Codex-style rows: failures unknown, not 0
        self.assertIn("tool_failures", run.data.tags["orca.unavailable_metrics"])

    def test_calls_stored_before_the_migration_leave_tool_totals_unknown(self):
        self.add_session("a", ["2026-10-09T01:10:00Z"])
        self.conn.execute("UPDATE observability.orca_call_events SET tool_calls = NULL, tool_calls_total = NULL, tool_failures = NULL")
        self.run_all()
        run = self.runs()[self.task("a")]
        self.assertFalse([k for k in run.data.metrics if k.startswith("tool")])
        self.assertIn("tool_calls_total", run.data.tags["orca.unavailable_metrics"])

    def test_old_sessions_are_left_alone(self):
        self.add_session("old", ["2026-01-01T01:00:00Z"])
        self.conn.execute("UPDATE observability.orca_call_events SET occurred_at = now() - interval '30 days' WHERE session_id_hash = %s",
                          (orca_calls._hash(self.sessions["old"]),))
        self.run_all()
        self.assertEqual(self.status("old"), {"unassigned/-": 1})


if __name__ == "__main__":
    unittest.main()
