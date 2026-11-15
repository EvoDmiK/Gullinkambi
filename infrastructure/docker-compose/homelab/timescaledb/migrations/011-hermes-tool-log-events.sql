-- Additive: tool calls as recorded in Hermes' own log (agent.tool_executor). Unlike tool results in
-- state.db, the log reports every failed call for every tool (MCP included) with its duration; successful
-- calls are only partly logged, so durations and counts here are "as logged", not complete.
-- Only the tool name, session hash, status, duration and result size are stored; error text is never sent.
-- Apply with: psql -v ON_ERROR_STOP=1 -v app_user=<role> -f 011-hermes-tool-log-events.sql
-- Rollback:   DROP FUNCTION observability.ingest_hermes_tool_log_event(jsonb, text, timestamptz);
--             DROP TABLE observability.hermes_tool_log_events; then re-create the dispatcher from migration 009.
CREATE TABLE IF NOT EXISTS observability.hermes_tool_log_events (
    event_key text PRIMARY KEY,
    schema_version integer NOT NULL,
    profile text NOT NULL,
    session_hash text NOT NULL,
    occurred_at timestamptz NOT NULL,
    tool text NOT NULL,
    status text NOT NULL CHECK (status IN ('ok', 'error')),
    duration_ms bigint CHECK (duration_ms >= 0),
    result_chars bigint CHECK (result_chars >= 0),
    collector_id text NOT NULL,
    first_observed_at timestamptz NOT NULL
);
CREATE INDEX IF NOT EXISTS hermes_tool_log_events_time_idx ON observability.hermes_tool_log_events (occurred_at DESC);
CREATE INDEX IF NOT EXISTS hermes_tool_log_events_session_idx ON observability.hermes_tool_log_events (session_hash, occurred_at DESC);

CREATE OR REPLACE FUNCTION observability.ingest_hermes_tool_log_event(
    item jsonb,
    incoming_collector_id text,
    observed_at timestamptz
) RETURNS boolean
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = observability, pg_temp
AS $$
DECLARE
    changed_rows integer := 0;
BEGIN
    IF COALESCE(item->>'event_key', '') = ''
       OR COALESCE(item->>'session_hash', '') = ''
       OR COALESCE(item->>'profile', '') = ''
       OR COALESCE(item->>'tool', '') = ''
       OR COALESCE(item->>'occurred_at', '') = ''
       OR COALESCE(item->>'status', '') NOT IN ('ok', 'error') THEN
        RAISE EXCEPTION 'invalid Hermes tool log event payload';
    END IF;

    INSERT INTO observability.hermes_tool_log_events (
        event_key, schema_version, profile, session_hash, occurred_at, tool, status, duration_ms, result_chars,
        collector_id, first_observed_at
    ) VALUES (
        item->>'event_key', COALESCE((item->>'schema_version')::integer, 1), item->>'profile',
        item->>'session_hash', (item->>'occurred_at')::timestamptz, left(item->>'tool', 64), item->>'status',
        (item->>'duration_ms')::bigint, (item->>'result_chars')::bigint, incoming_collector_id, observed_at
    )
    ON CONFLICT (event_key) DO NOTHING;
    GET DIAGNOSTICS changed_rows = ROW_COUNT;
    RETURN changed_rows > 0;
END;
$$;

REVOKE ALL ON FUNCTION observability.ingest_hermes_tool_log_event(jsonb, text, timestamptz) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION observability.ingest_hermes_tool_log_event(jsonb, text, timestamptz) TO :"app_user";

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
        WHEN 'hermes_session_sample' THEN
            RETURN observability.ingest_hermes_session_sample(item, incoming_collector_id, observed_at);
        WHEN 'hermes_tool_event' THEN
            RETURN observability.ingest_hermes_tool_event(item, incoming_collector_id, observed_at);
        WHEN 'hermes_tool_log_event' THEN
            RETURN observability.ingest_hermes_tool_log_event(item, incoming_collector_id, observed_at);
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

DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'grafana_reader') THEN
        GRANT SELECT ON observability.hermes_tool_log_events TO grafana_reader;
    END IF;
END;
$$;
