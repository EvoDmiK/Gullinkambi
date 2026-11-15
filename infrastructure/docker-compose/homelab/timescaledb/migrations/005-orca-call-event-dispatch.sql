-- Additive: route the 'orca_call_event' entity kind to ingest_orca_call_event (migration 001).
-- Must stay identical to the dispatcher in init-telemetry.sh, which re-creates it on every re-run
-- (tests/test_dispatcher_sync.py checks this). Requires migration 001.
-- Apply with: psql -v ON_ERROR_STOP=1 -v app_user=<role> -f 005-orca-call-event-dispatch.sql
-- Rollback:   re-run init-telemetry.sh from before this change, or re-create the dispatcher without that WHEN branch.
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
