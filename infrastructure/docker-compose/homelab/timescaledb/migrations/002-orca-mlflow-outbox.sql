-- Additive: complete attempt snapshots + MLflow outbox/run mapping.
-- Apply with: psql -v ON_ERROR_STOP=1 -v app_user=<role> -f 002-orca-mlflow-outbox.sql
-- Rollback:   DROP FUNCTION observability.record_orca_attempt_snapshot(jsonb);
--             DROP TABLE observability.orca_mlflow_outbox, observability.orca_mlflow_runs,
--                        observability.orca_attempt_snapshots;
CREATE TABLE IF NOT EXISTS observability.orca_attempt_snapshots (
    task_id text NOT NULL,
    attempt_id text NOT NULL,
    revision integer NOT NULL CHECK (revision >= 1),
    fingerprint text NOT NULL,
    snapshot jsonb NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (task_id, attempt_id, revision)
);

-- Source of truth for "which Run is this attempt". correlation_key is also sent as a
-- Run tag, but MLflow tags are not unique server-side, so this table is authoritative.
CREATE TABLE IF NOT EXISTS observability.orca_mlflow_runs (
    task_id text NOT NULL,
    attempt_id text NOT NULL,
    experiment text NOT NULL,
    correlation_key text NOT NULL UNIQUE,
    run_id text,
    create_outcome text NOT NULL CHECK (create_outcome IN ('intent', 'created', 'uncertain', 'conflict')),
    reconcile_attempts integer NOT NULL DEFAULT 0,
    committed_snapshot_revision integer,
    supersedes_run_id text,
    updated_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (task_id, attempt_id, experiment)
);

CREATE TABLE IF NOT EXISTS observability.orca_mlflow_outbox (
    task_id text NOT NULL,
    attempt_id text NOT NULL,
    revision integer NOT NULL,
    state text NOT NULL DEFAULT 'pending'
        CHECK (state IN ('pending', 'sent', 'superseded', 'dead')),
    attempt_count integer NOT NULL DEFAULT 0,
    next_attempt_at timestamptz NOT NULL DEFAULT now(),
    last_error_code text,
    updated_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (task_id, attempt_id, revision),
    FOREIGN KEY (task_id, attempt_id, revision)
        REFERENCES observability.orca_attempt_snapshots (task_id, attempt_id, revision)
);
CREATE INDEX IF NOT EXISTS orca_mlflow_outbox_pending_idx
    ON observability.orca_mlflow_outbox (next_attempt_at) WHERE state = 'pending';

-- Stores a new snapshot revision and its outbox row in ONE transaction. A snapshot
-- identical (by fingerprint) to the latest revision is a no-op and returns NULL.
CREATE OR REPLACE FUNCTION observability.record_orca_attempt_snapshot(snapshot jsonb)
RETURNS integer
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = observability, pg_temp
AS $$
DECLARE
    t text := snapshot->>'task_id';
    a text := snapshot->>'attempt_id';
    fp text := snapshot->>'fingerprint';
    latest record;
    next_revision integer;
BEGIN
    IF COALESCE(t, '') = '' OR COALESCE(a, '') = '' OR COALESCE(fp, '') = '' THEN
        RAISE EXCEPTION 'invalid Orca attempt snapshot';
    END IF;
    PERFORM pg_advisory_xact_lock(hashtextextended(t || E'\x1f' || a, 0));
    SELECT revision, fingerprint INTO latest
    FROM observability.orca_attempt_snapshots
    WHERE task_id = t AND attempt_id = a ORDER BY revision DESC LIMIT 1;
    IF FOUND AND latest.fingerprint = fp THEN
        RETURN NULL;
    END IF;
    next_revision := COALESCE(latest.revision, 0) + 1;
    INSERT INTO observability.orca_attempt_snapshots (task_id, attempt_id, revision, fingerprint, snapshot)
    VALUES (t, a, next_revision, fp, snapshot || jsonb_build_object('revision', next_revision));
    INSERT INTO observability.orca_mlflow_outbox (task_id, attempt_id, revision)
    VALUES (t, a, next_revision);
    RETURN next_revision;
END;
$$;

REVOKE ALL ON FUNCTION observability.record_orca_attempt_snapshot(jsonb) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION observability.record_orca_attempt_snapshot(jsonb) TO :"app_user";

DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'grafana_reader') THEN
        GRANT SELECT ON observability.orca_attempt_snapshots, observability.orca_mlflow_runs,
            observability.orca_mlflow_outbox TO grafana_reader;
    END IF;
END;
$$;
