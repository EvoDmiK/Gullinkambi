"""Single-process outbox → MLflow exporter (at-least-once, reconciled, read-back verified).

Delivery guarantee: NOT exactly-once. A lost create response leaves the Run mapping in
`uncertain`; the exporter then only *searches* by correlation tag and never re-creates
blindly. Zero candidates after `reconcile_limit` searches, or several candidates, block
the attempt (dead-letter) for an operator instead of guessing.

Needs `psycopg` (v3) and `mlflow` at runtime; the MLflow client is injected so tests
can wrap it with fault injection. Snapshots are complete, so only the latest pending
revision of an attempt is published; older pending revisions become `superseded`, and
the committed-revision pointer never moves backwards.
"""
from __future__ import annotations

import hashlib
import json
import re
import tempfile
import time
from pathlib import Path
from typing import Any

TERMINAL_STATUSES = {"completed", "failed", "cancelled", "blocked"}
CORRELATION_TAG = "orca.correlation_key"
COMMITTED_TAG = "orca.committed_revision"
ADVISORY_LOCK_KEY = 0x0CA_0B5  # one exporter at a time


class Retry(Exception):
    """Transient problem; retried with backoff."""


class Blocked(Exception):
    """Needs an operator (conflict / unresolved create). Dead-letters the outbox row."""


def correlation_key(experiment: str, task_id: str, attempt_id: str) -> str:
    raw = "\x1f".join((experiment, task_id, attempt_id))
    return hashlib.sha256(raw.encode()).hexdigest()[:32]


def snapshot_tags(snapshot: dict[str, Any]) -> dict[str, str]:
    unknown = sorted(k for k, v in snapshot["availability"].items() if v != "complete")
    tags = {
        "mlflow.runName": f"{snapshot['task_id']}/{snapshot['attempt_id']}",  # readable name instead of a random one
        "orca.schema_version": str(snapshot["schema_version"]),
        "orca.task_id": snapshot["task_id"], "orca.attempt_id": snapshot["attempt_id"],
        "orca.project_key": snapshot["project_key"],
        "orca.agents": ",".join(snapshot["agents"]),
        "orca.reported_status": snapshot["reported_status"],
        "orca.verification_state": snapshot["verification_state"],
        "orca.usage_coverage": snapshot["usage_coverage"],
        "orca.latest_snapshot_revision": str(snapshot["revision"]),
        "orca.unavailable_metrics": ",".join(unknown),
    }
    if snapshot.get("origin"):
        tags["orca.origin"] = snapshot["origin"]
    return tags


def snapshot_metrics(snapshot: dict[str, Any]) -> dict[str, float]:
    metrics = {k: float(v) for k, v in snapshot["metrics"].items()}
    if snapshot["request_count"] is not None:
        metrics["request_count"] = float(snapshot["request_count"])
    for name, count in sorted((snapshot.get("tools") or {}).items(), key=lambda kv: -kv[1])[:10]:
        metrics["tool." + re.sub(r"[^A-Za-z0-9_.\- :/]", "_", name)] = float(count)  # top 10 as metrics, all in the artifact
    for key, value in snapshot.get("test", {}).items():
        if key != "test_id":
            metrics[f"test_{key}"] = float(value)
    return metrics  # unknown values are simply absent; availability tags say why


class Exporter:
    def __init__(self, conn, client, experiment_name: str, *, max_attempts=8, reconcile_limit=3):
        self.conn, self.client = conn, client
        self.experiment = experiment_name
        self.max_attempts, self.reconcile_limit = max_attempts, reconcile_limit
        self._experiment_id: str | None = None

    # -- helpers -----------------------------------------------------------
    def experiment_id(self) -> str:
        if self._experiment_id is None:
            found = self.client.get_experiment_by_name(self.experiment)
            self._experiment_id = found.experiment_id if found else self.client.create_experiment(self.experiment)
        return self._experiment_id

    def _one(self, sql, params=()):
        return self.conn.execute(sql, params).fetchone()

    # -- run mapping -------------------------------------------------------
    def ensure_run(self, task_id: str, attempt_id: str) -> str:
        key = correlation_key(self.experiment, task_id, attempt_id)
        with self.conn.transaction():
            self.conn.execute(
                """INSERT INTO observability.orca_mlflow_runs
                   (task_id, attempt_id, experiment, correlation_key, create_outcome)
                   VALUES (%s, %s, %s, %s, 'intent') ON CONFLICT DO NOTHING""",
                (task_id, attempt_id, self.experiment, key))
            outcome, run_id = self._one(
                """SELECT create_outcome, run_id FROM observability.orca_mlflow_runs
                   WHERE task_id=%s AND attempt_id=%s AND experiment=%s FOR UPDATE""",
                (task_id, attempt_id, self.experiment))
        if outcome == "created":
            return run_id
        if outcome == "conflict":
            raise Blocked("run_conflict")
        if outcome == "intent":
            experiment_id = self.experiment_id()  # failure here precedes any create request: stay `intent`
            try:
                run = self.client.create_run(experiment_id, tags={
                    CORRELATION_TAG: key, "orca.task_id": task_id, "orca.attempt_id": attempt_id})
            except Exception:
                self._set_outcome(task_id, attempt_id, "uncertain")
                return self._reconcile(task_id, attempt_id, key)
            self._set_outcome(task_id, attempt_id, "created", run.info.run_id)
            return run.info.run_id
        return self._reconcile(task_id, attempt_id, key)

    def _set_outcome(self, task_id, attempt_id, outcome, run_id=None):
        with self.conn.transaction():
            self.conn.execute(
                """UPDATE observability.orca_mlflow_runs
                   SET create_outcome=%s, run_id=COALESCE(%s, run_id), updated_at=now()
                   WHERE task_id=%s AND attempt_id=%s AND experiment=%s""",
                (outcome, run_id, task_id, attempt_id, self.experiment))

    def _reconcile(self, task_id, attempt_id, key) -> str:
        try:
            runs = self.client.search_runs(
                [self.experiment_id()], filter_string=f"tags.`{CORRELATION_TAG}` = '{key}'")
        except Exception as exc:
            raise Retry("reconcile_search_failed") from exc
        if len(runs) > 1:
            self._set_outcome(task_id, attempt_id, "conflict")
            raise Blocked("duplicate_run_candidates")
        if len(runs) == 1:
            run_id = runs[0].info.run_id
            self._set_outcome(task_id, attempt_id, "created", run_id)
            return run_id
        with self.conn.transaction():
            (attempts,) = self._one(
                """UPDATE observability.orca_mlflow_runs SET reconcile_attempts = reconcile_attempts + 1,
                   updated_at=now() WHERE task_id=%s AND attempt_id=%s AND experiment=%s
                   RETURNING reconcile_attempts""", (task_id, attempt_id, self.experiment))
        # Zero candidates is not proof the Run was never created (search may lag).
        if attempts >= self.reconcile_limit:
            raise Blocked("create_uncertain_unresolved")
        raise Retry("create_uncertain")

    # -- publishing --------------------------------------------------------
    def publish(self, run_id: str, snapshot: dict[str, Any]) -> None:
        from mlflow.entities import Metric, RunTag

        revision = snapshot["revision"]
        now_ms = int(time.time() * 1000)  # step carries the revision; replays only add equal history points
        self.client.log_batch(
            run_id,
            metrics=[Metric(k, v, now_ms, revision) for k, v in snapshot_metrics(snapshot).items()],
            tags=[RunTag(k, v) for k, v in snapshot_tags(snapshot).items()])
        artifact_path = f"snapshots/rev-{revision:06d}.json"
        self.client.log_dict(run_id, snapshot, artifact_path)
        self._verify(run_id, snapshot, artifact_path)
        self.client.set_tag(run_id, COMMITTED_TAG, str(revision))
        # FINISHED means "this snapshot is complete", not "passed". Automatic session attempts have no
        # reported end, so they would otherwise look permanently RUNNING; later revisions still update them.
        if snapshot["reported_status"] in TERMINAL_STATUSES or snapshot.get("origin") == "auto":
            self.client.set_terminated(run_id, "FINISHED")

    def _verify(self, run_id: str, snapshot: dict[str, Any], artifact_path: str) -> None:
        run = self.client.get_run(run_id)
        for key, expected in snapshot_tags(snapshot).items():
            if run.data.tags.get(key) != expected:
                raise Retry(f"readback_tag_mismatch:{key}")
        for key, expected in snapshot_metrics(snapshot).items():
            history = self.client.get_metric_history(run_id, key)
            if not any(m.step == snapshot["revision"] and m.value == expected for m in history):
                raise Retry(f"readback_metric_mismatch:{key}")
        with tempfile.TemporaryDirectory() as tmp:
            local = self.client.download_artifacts(run_id, artifact_path, tmp)
            if json.loads(Path(local).read_text()) != snapshot:
                raise Retry("readback_artifact_mismatch")

    # -- queue -------------------------------------------------------------
    def run_once(self) -> dict[str, int]:
        counts = {"sent": 0, "retry": 0, "dead": 0, "stale": 0, "locked": 0}
        (locked,) = self._one("SELECT pg_try_advisory_lock(%s)", (ADVISORY_LOCK_KEY,))
        if not locked:
            counts["locked"] = 1
            return counts
        try:
            due = self.conn.execute(
                """SELECT DISTINCT ON (task_id, attempt_id) task_id, attempt_id, revision, next_attempt_at <= now()
                   FROM observability.orca_mlflow_outbox WHERE state='pending'
                   ORDER BY task_id, attempt_id, revision DESC""").fetchall()
            for task_id, attempt_id, revision, is_due in due:
                if is_due:
                    counts[self._deliver(task_id, attempt_id, revision)] += 1
        finally:
            self.conn.execute("SELECT pg_advisory_unlock(%s)", (ADVISORY_LOCK_KEY,))
        return counts

    def _deliver(self, task_id: str, attempt_id: str, revision: int) -> str:
        (snapshot,) = self._one(
            "SELECT snapshot FROM observability.orca_attempt_snapshots WHERE task_id=%s AND attempt_id=%s AND revision=%s",
            (task_id, attempt_id, revision))
        try:
            row = self._one(
                """SELECT committed_snapshot_revision FROM observability.orca_mlflow_runs
                   WHERE task_id=%s AND attempt_id=%s AND experiment=%s""",
                (task_id, attempt_id, self.experiment))
            committed = row[0] if row else None
            if committed is not None and committed >= revision:
                self._finish(task_id, attempt_id, revision)  # replay of an old revision: never go backwards
                return "stale"
            run_id = self.ensure_run(task_id, attempt_id)
            self.publish(run_id, snapshot)
            with self.conn.transaction():
                self.conn.execute(
                    """UPDATE observability.orca_mlflow_runs SET committed_snapshot_revision =
                       GREATEST(COALESCE(committed_snapshot_revision, 0), %s), updated_at=now()
                       WHERE task_id=%s AND attempt_id=%s AND experiment=%s""",
                    (revision, task_id, attempt_id, self.experiment))
            self._finish(task_id, attempt_id, revision)
            return "sent"
        except Blocked as exc:
            self._fail(task_id, attempt_id, revision, str(exc), dead=True)
            return "dead"
        except Exception as exc:
            code = str(exc) if isinstance(exc, Retry) else type(exc).__name__  # never raw messages
            return self._fail(task_id, attempt_id, revision, code[:120], dead=False)

    def _finish(self, task_id, attempt_id, revision):
        with self.conn.transaction():
            self.conn.execute(
                """UPDATE observability.orca_mlflow_outbox SET updated_at=now(),
                   state = CASE WHEN revision = %s THEN 'sent' ELSE 'superseded' END
                   WHERE task_id=%s AND attempt_id=%s AND revision <= %s AND state='pending'""",
                (revision, task_id, attempt_id, revision))

    def _fail(self, task_id, attempt_id, revision, code, dead: bool) -> str:
        with self.conn.transaction():
            (count,) = self._one(
                """UPDATE observability.orca_mlflow_outbox SET attempt_count = attempt_count + 1,
                   last_error_code=%s, updated_at=now(),
                   next_attempt_at = now() + least(interval '1 hour', interval '30 seconds' * power(2, attempt_count))
                   WHERE task_id=%s AND attempt_id=%s AND revision=%s RETURNING attempt_count""",
                (code, task_id, attempt_id, revision))
            if dead or count >= self.max_attempts:
                self.conn.execute(
                    """UPDATE observability.orca_mlflow_outbox SET state='dead'
                       WHERE task_id=%s AND attempt_id=%s AND revision=%s""", (task_id, attempt_id, revision))
                return "dead"
        return "retry"

    def backlog(self) -> dict[str, Any]:
        pending, oldest = self._one(
            """SELECT count(*), COALESCE(extract(epoch FROM now() - min(updated_at)), 0)
               FROM observability.orca_mlflow_outbox WHERE state='pending'""")
        (dead,) = self._one("SELECT count(*) FROM observability.orca_mlflow_outbox WHERE state='dead'")
        return {"pending": pending, "oldest_pending_age_seconds": float(oldest), "dead": dead}


def main() -> int:
    import os
    import sys
    from urllib.parse import urlparse

    import psycopg
    from mlflow import MlflowClient

    dsn, uri = os.getenv("ORCA_PIPELINE_DSN"), os.getenv("MLFLOW_TRACKING_URI")
    if not dsn or not uri:
        print("mlflow_exporter: ORCA_PIPELINE_DSN and MLFLOW_TRACKING_URI are required", file=sys.stderr)
        return 2
    if urlparse(uri).hostname not in ("127.0.0.1", "localhost", "::1") and os.getenv("ORCA_EXPORT_ALLOW_REMOTE") != "1":
        print("mlflow_exporter: refusing non-loopback MLflow; set ORCA_EXPORT_ALLOW_REMOTE=1 after approval", file=sys.stderr)
        return 2
    experiment = os.getenv("ORCA_MLFLOW_EXPERIMENT", "orca-dev-observability")
    interval = int(os.getenv("ORCA_EXPORT_INTERVAL_SECONDS", "0"))  # 0 = run once
    client = MlflowClient(uri)
    while True:
        try:
            # one short-lived connection per pass, so a restarted DB never leaves a dead session
            with psycopg.connect(dsn, autocommit=True) as conn:
                if os.getenv("ORCA_PIPELINE_FROM_DB") == "1":
                    # attribute calls to the attempts reported via orca_task.py, then publish the snapshots
                    import orca_pipeline

                    approved = {v for v in os.getenv("ORCA_APPROVED_VERIFIERS", "").split(",") if v}
                    key = os.getenv("ORCA_VERIFIER_KEY") or None
                    counts = orca_pipeline.run_pipeline(
                        conn, orca_pipeline.load_inputs_from_db(conn, verifier_key=key), approved, key,
                        auto_sessions=os.getenv("ORCA_AUTO_SESSIONS") == "1")
                    print(json.dumps({"pipeline": counts}, sort_keys=True), flush=True)
                exporter = Exporter(conn, client, experiment)
                print(json.dumps({**exporter.run_once(), **exporter.backlog()}, sort_keys=True), flush=True)
        except Exception as exc:  # keep the daemon alive; log the class only, never the message
            print(json.dumps({"error": type(exc).__name__}), flush=True)
        if interval <= 0:
            return 0
        time.sleep(interval)


if __name__ == "__main__":
    raise SystemExit(main())
