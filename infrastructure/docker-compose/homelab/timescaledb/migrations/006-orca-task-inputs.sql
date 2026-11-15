-- Additive: task/attempt manifests and agent-reported results, received through the existing
-- telemetry webhook (orca_task.py -> n8n -> ingest_hermes_telemetry). Requires migrations 001-004.
-- The dispatcher below must stay identical to the one in init-telemetry.sh, which re-creates it on
-- every re-run (tests/test_collector_orca_calls.py checks the latest migration against it).
-- Apply with: ORCA_EXPORTER_PASSWORD is NOT needed here; pass the role to grant to:
--   psql -v ON_ERROR_STOP=1 -v app_user=<app role> -v exporter_user=orca_exporter -f 006-orca-task-inputs.sql
-- Rollback: re-create the previous dispatcher (migration 005 text), then
--   DROP FUNCTION observability.ingest_orca_task_manifest(jsonb, text, timestamptz);
--   DROP FUNCTION observability.ingest_orca_task_result(jsonb, text, timestamptz);
--   DROP TABLE observability.orca_task_results, observability.orca_task_manifests;

CREATE TABLE IF NOT EXISTS observability.orca_task_manifests (
    task_id text NOT NULL,
    attempt_id text NOT NULL,
    revision integer NOT NULL CHECK (revision >= 1),
    producer_id text NOT NULL,
    manifest jsonb NOT NULL,
    collector_id text NOT NULL,
    received_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (task_id, attempt_id, revision)
);

CREATE TABLE IF NOT EXISTS observability.orca_task_results (
    event_id text PRIMARY KEY,
    task_id text NOT NULL,
    attempt_id text NOT NULL,
    result_revision integer NOT NULL CHECK (result_revision >= 1),
    producer_id text NOT NULL,
    result jsonb NOT NULL,
    collector_id text NOT NULL,
    received_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS orca_task_results_attempt_idx
    ON observability.orca_task_results (task_id, attempt_id, result_revision DESC);

-- Revisions are immutable: a replay of the same (task, attempt, revision) or event_id is a no-op,
-- so a corrected manifest must use a higher revision. Defense in depth: ID shape and size are
-- checked here too, although orca_pipeline validates every document again when it reads it.
CREATE OR REPLACE FUNCTION observability.ingest_orca_task_manifest(
    item jsonb, incoming_collector_id text, observed_at timestamptz
) RETURNS boolean
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = observability, pg_temp
AS $$
DECLARE
    id_pattern constant text := '^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$';
    changed_rows integer := 0;
BEGIN
    IF length(item::text) > 16384
       OR COALESCE(item->>'task_id', '') !~ id_pattern
       OR COALESCE(item->>'attempt_id', '') !~ id_pattern
       OR COALESCE(item->>'producer_id', '') !~ id_pattern
       OR COALESCE(item->>'revision', '') !~ '^[0-9]{1,6}$' THEN
        RAISE EXCEPTION 'invalid Orca task manifest payload';
    END IF;
    INSERT INTO observability.orca_task_manifests
        (task_id, attempt_id, revision, producer_id, manifest, collector_id, received_at)
    VALUES (item->>'task_id', item->>'attempt_id', (item->>'revision')::integer,
            item->>'producer_id', item, incoming_collector_id, observed_at)
    ON CONFLICT (task_id, attempt_id, revision) DO NOTHING;
    GET DIAGNOSTICS changed_rows = ROW_COUNT;
    RETURN changed_rows > 0;
END;
$$;

CREATE OR REPLACE FUNCTION observability.ingest_orca_task_result(
    item jsonb, incoming_collector_id text, observed_at timestamptz
) RETURNS boolean
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = observability, pg_temp
AS $$
DECLARE
    id_pattern constant text := '^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$';
    changed_rows integer := 0;
BEGIN
    IF length(item::text) > 16384
       OR COALESCE(item->>'event_id', '') !~ id_pattern
       OR COALESCE(item->>'task_id', '') !~ id_pattern
       OR COALESCE(item->>'attempt_id', '') !~ id_pattern
       OR COALESCE(item->>'producer_id', '') !~ id_pattern
       OR COALESCE(item->>'result_revision', '') !~ '^[0-9]{1,6}$' THEN
        RAISE EXCEPTION 'invalid Orca task result payload';
    END IF;
    INSERT INTO observability.orca_task_results
        (event_id, task_id, attempt_id, result_revision, producer_id, result, collector_id, received_at)
    VALUES (item->>'event_id', item->>'task_id', item->>'attempt_id', (item->>'result_revision')::integer,
            item->>'producer_id', item, incoming_collector_id, observed_at)
    ON CONFLICT (event_id) DO NOTHING;
    GET DIAGNOSTICS changed_rows = ROW_COUNT;
    RETURN changed_rows > 0;
END;
$$;

REVOKE ALL ON FUNCTION observability.ingest_orca_task_manifest(jsonb, text, timestamptz) FROM PUBLIC;
REVOKE ALL ON FUNCTION observability.ingest_orca_task_result(jsonb, text, timestamptz) FROM PUBLIC;

CREATE OR REPLACE FUNCTION observability.ingest_hermes_telemetry(
    entity_kind text,
    item jsonb,
    incoming_collector_id text,
    observed_at timestamptz
) RETURNS boolean
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = observability, pg_temp
AS $$
BEGIN
    CASE entity_kind
        WHEN 'run' THEN
            RETURN observability.ingest_hermes_run(item, incoming_collector_id, observed_at);
        WHEN 'goal_run' THEN
            RETURN observability.ingest_hermes_goal_run(item, incoming_collector_id, observed_at);
        WHEN 'orca_usage' THEN
            RETURN observability.ingest_orca_usage(item, incoming_collector_id, observed_at);
        WHEN 'orca_thread_usage' THEN
            RETURN observability.ingest_orca_thread_usage(item, incoming_collector_id, observed_at);
        WHEN 'orca_call_event' THEN
            RETURN observability.ingest_orca_call_event(item, incoming_collector_id, observed_at);
        WHEN 'orca_task_manifest' THEN
            RETURN observability.ingest_orca_task_manifest(item, incoming_collector_id, observed_at);
        WHEN 'orca_task_result' THEN
            RETURN observability.ingest_orca_task_result(item, incoming_collector_id, observed_at);
        WHEN 'kanban_task' THEN
            RETURN observability.ingest_hermes_kanban_task(item, incoming_collector_id, observed_at);
        WHEN 'kanban_task_run' THEN
            RETURN observability.ingest_hermes_kanban_task_run(item, incoming_collector_id, observed_at);
        WHEN 'kanban_task_event' THEN
            RETURN observability.ingest_hermes_kanban_task_event(item, incoming_collector_id, observed_at);
        WHEN 'kanban_task_link' THEN
            RETURN observability.ingest_hermes_kanban_task_link(item, incoming_collector_id, observed_at);
        ELSE
            RAISE EXCEPTION 'unsupported Hermes telemetry entity kind: %', entity_kind;
    END CASE;
END;
$$;

REVOKE ALL ON FUNCTION observability.ingest_hermes_telemetry(text, jsonb, text, timestamptz) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION observability.ingest_hermes_telemetry(text, jsonb, text, timestamptz) TO :"app_user";

-- The exporter container also runs the attribution pipeline from these tables. It reads inputs and
-- events and writes only through the two SECURITY DEFINER functions (no direct table writes).
SELECT set_config('orca.exporter_user', :'exporter_user', false) \g /dev/null
DO $$
DECLARE
    exporter text := current_setting('orca.exporter_user');
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = exporter) THEN
        EXECUTE format('GRANT SELECT ON observability.orca_call_events, observability.orca_task_manifests, observability.orca_task_results TO %I', exporter);
        EXECUTE format('GRANT EXECUTE ON FUNCTION observability.assign_orca_call_attributions(jsonb) TO %I', exporter);
        EXECUTE format('GRANT EXECUTE ON FUNCTION observability.record_orca_attempt_snapshot(jsonb) TO %I', exporter);
    ELSE
        RAISE NOTICE 'role % does not exist; skipped exporter grants (re-run after migration 004)', exporter;
    END IF;
END;
$$;

DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'grafana_reader') THEN
        GRANT SELECT ON observability.orca_task_manifests, observability.orca_task_results TO grafana_reader;
    END IF;
END;
$$;
