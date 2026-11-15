-- Additive: Hermes session samples and tool events (metadata only). The old observability.hermes_runs
-- table is left untouched as an archive; nothing here reads or writes it.
-- Hermes only exposes cumulative per-session counters, so usage over time is the difference between
-- consecutive samples (view hermes_usage_deltas). Session ids are hashed; no titles, names, chat ids,
-- prompts, tool arguments or tool results are stored.
-- Apply with: psql -v ON_ERROR_STOP=1 -v app_user=<role> -f 009-hermes-sessions.sql
-- Rollback:   DROP VIEW observability.hermes_usage_deltas, observability.hermes_sessions_current;
--             DROP FUNCTION observability.ingest_hermes_session_sample(jsonb, text, timestamptz);
--             DROP FUNCTION observability.ingest_hermes_tool_event(jsonb, text, timestamptz);
--             DROP TABLE observability.hermes_session_samples, observability.hermes_tool_events;
--             then re-create the dispatcher from migration 006.
CREATE TABLE IF NOT EXISTS observability.hermes_session_samples (
    event_key text PRIMARY KEY,
    schema_version integer NOT NULL,
    profile text NOT NULL,
    session_hash text NOT NULL,
    parent_hash text,
    source text NOT NULL,
    chat_type text,
    model text NOT NULL,
    provider text NOT NULL,
    project_key text,
    started_at timestamptz NOT NULL,
    last_activity_at timestamptz NOT NULL,
    ended_at timestamptz,
    end_reason text,
    expiry_finalized boolean NOT NULL DEFAULT false,
    archived boolean NOT NULL DEFAULT false,
    sampled_at timestamptz NOT NULL,
    message_count bigint NOT NULL CHECK (message_count >= 0),
    tool_call_count bigint NOT NULL CHECK (tool_call_count >= 0),
    api_call_count bigint NOT NULL CHECK (api_call_count >= 0),
    input_tokens bigint NOT NULL CHECK (input_tokens >= 0),
    output_tokens bigint NOT NULL CHECK (output_tokens >= 0),
    cache_read_tokens bigint NOT NULL CHECK (cache_read_tokens >= 0),
    cache_write_tokens bigint NOT NULL CHECK (cache_write_tokens >= 0),
    reasoning_tokens bigint NOT NULL CHECK (reasoning_tokens >= 0),
    estimated_cost_usd double precision NOT NULL DEFAULT 0,
    actual_cost_usd double precision NOT NULL DEFAULT 0,
    cost_status text,
    fingerprint text NOT NULL,
    collector_id text NOT NULL,
    first_observed_at timestamptz NOT NULL
);
CREATE INDEX IF NOT EXISTS hermes_session_samples_session_idx
    ON observability.hermes_session_samples (session_hash, sampled_at);
CREATE INDEX IF NOT EXISTS hermes_session_samples_time_idx
    ON observability.hermes_session_samples (sampled_at DESC);

CREATE TABLE IF NOT EXISTS observability.hermes_tool_events (
    event_key text PRIMARY KEY,
    schema_version integer NOT NULL,
    profile text NOT NULL,
    session_hash text NOT NULL,
    occurred_at timestamptz NOT NULL,
    tool text NOT NULL,
    -- ok | error | nonzero_exit | unknown; derived on the collector from the shape of the result.
    outcome text NOT NULL CHECK (outcome IN ('ok', 'error', 'nonzero_exit', 'unknown')),
    collector_id text NOT NULL,
    first_observed_at timestamptz NOT NULL
);
CREATE INDEX IF NOT EXISTS hermes_tool_events_time_idx ON observability.hermes_tool_events (occurred_at DESC);
CREATE INDEX IF NOT EXISTS hermes_tool_events_session_idx ON observability.hermes_tool_events (session_hash, occurred_at DESC);

-- Append-only: a replayed sample or event changes nothing.
CREATE OR REPLACE FUNCTION observability.ingest_hermes_session_sample(
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
       OR COALESCE(item->>'source', '') = ''
       OR COALESCE(item->>'model', '') = ''
       OR COALESCE(item->>'sampled_at', '') = ''
       OR COALESCE(item->>'started_at', '') = '' THEN
        RAISE EXCEPTION 'invalid Hermes session sample payload';
    END IF;

    INSERT INTO observability.hermes_session_samples (
        event_key, schema_version, profile, session_hash, parent_hash, source, chat_type, model, provider,
        project_key, started_at, last_activity_at, ended_at, end_reason, expiry_finalized, archived, sampled_at,
        message_count, tool_call_count, api_call_count, input_tokens, output_tokens, cache_read_tokens,
        cache_write_tokens, reasoning_tokens, estimated_cost_usd, actual_cost_usd, cost_status,
        fingerprint, collector_id, first_observed_at
    ) VALUES (
        item->>'event_key', COALESCE((item->>'schema_version')::integer, 1), item->>'profile',
        item->>'session_hash', NULLIF(item->>'parent_hash', ''), item->>'source', NULLIF(item->>'chat_type', ''),
        item->>'model', COALESCE(item->>'provider', 'unknown'), NULLIF(item->>'project_key', ''),
        (item->>'started_at')::timestamptz,
        COALESCE((item->>'last_activity_at')::timestamptz, (item->>'started_at')::timestamptz),
        (item->>'ended_at')::timestamptz, NULLIF(item->>'end_reason', ''),
        COALESCE((item->>'expiry_finalized')::boolean, false), COALESCE((item->>'archived')::boolean, false),
        (item->>'sampled_at')::timestamptz,
        COALESCE((item->>'message_count')::bigint, 0), COALESCE((item->>'tool_call_count')::bigint, 0),
        COALESCE((item->>'api_call_count')::bigint, 0), COALESCE((item->>'input_tokens')::bigint, 0),
        COALESCE((item->>'output_tokens')::bigint, 0), COALESCE((item->>'cache_read_tokens')::bigint, 0),
        COALESCE((item->>'cache_write_tokens')::bigint, 0), COALESCE((item->>'reasoning_tokens')::bigint, 0),
        COALESCE((item->>'estimated_cost_usd')::double precision, 0),
        COALESCE((item->>'actual_cost_usd')::double precision, 0), NULLIF(item->>'cost_status', ''),
        COALESCE(item->>'fingerprint', ''), incoming_collector_id, observed_at
    )
    ON CONFLICT (event_key) DO NOTHING;
    GET DIAGNOSTICS changed_rows = ROW_COUNT;
    RETURN changed_rows > 0;
END;
$$;

CREATE OR REPLACE FUNCTION observability.ingest_hermes_tool_event(
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
       OR COALESCE(item->>'occurred_at', '') = '' THEN
        RAISE EXCEPTION 'invalid Hermes tool event payload';
    END IF;

    INSERT INTO observability.hermes_tool_events (
        event_key, schema_version, profile, session_hash, occurred_at, tool, outcome, collector_id, first_observed_at
    ) VALUES (
        item->>'event_key', COALESCE((item->>'schema_version')::integer, 1), item->>'profile',
        item->>'session_hash', (item->>'occurred_at')::timestamptz, left(item->>'tool', 64),
        COALESCE(item->>'outcome', 'unknown'), incoming_collector_id, observed_at
    )
    ON CONFLICT (event_key) DO NOTHING;
    GET DIAGNOSTICS changed_rows = ROW_COUNT;
    RETURN changed_rows > 0;
END;
$$;

REVOKE ALL ON FUNCTION observability.ingest_hermes_session_sample(jsonb, text, timestamptz) FROM PUBLIC;
REVOKE ALL ON FUNCTION observability.ingest_hermes_tool_event(jsonb, text, timestamptz) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION observability.ingest_hermes_session_sample(jsonb, text, timestamptz) TO :"app_user";
GRANT EXECUTE ON FUNCTION observability.ingest_hermes_tool_event(jsonb, text, timestamptz) TO :"app_user";

-- Latest sample per session plus a state that does not depend on Hermes ever writing ended_at:
--   ended  = ended_at is set or the session expired (expiry_finalized)
--   active = last activity within 1 hour
--   idle   = anything else (open but quiet; many Discord/CLI sessions are never closed by Hermes)
CREATE OR REPLACE VIEW observability.hermes_sessions_current AS
SELECT DISTINCT ON (session_hash)
    session_hash, profile, parent_hash, source, chat_type, model, provider, project_key, started_at,
    last_activity_at, ended_at, end_reason, expiry_finalized, archived, sampled_at AS last_sampled_at,
    message_count, tool_call_count, api_call_count, input_tokens, output_tokens, cache_read_tokens,
    cache_write_tokens, reasoning_tokens,
    input_tokens + output_tokens + reasoning_tokens + cache_read_tokens + cache_write_tokens AS total_tokens,
    estimated_cost_usd, actual_cost_usd, cost_status,
    CASE
        WHEN ended_at IS NOT NULL OR expiry_finalized THEN 'ended'
        WHEN last_activity_at >= now() - interval '1 hour' THEN 'active'
        ELSE 'idle'
    END AS state
FROM observability.hermes_session_samples
ORDER BY session_hash, sampled_at DESC;

-- Usage per sampling interval. The first sample of a session counts as new usage only when the session
-- started shortly before it; otherwise its counters were accumulated before sampling began and the
-- interval is unknown (NULL), never attributed to that moment. A counter that goes down yields 0.
CREATE OR REPLACE VIEW observability.hermes_usage_deltas AS
WITH ordered AS (
    SELECT s.*,
           lag(input_tokens) OVER w AS p_input, lag(output_tokens) OVER w AS p_output,
           lag(cache_read_tokens) OVER w AS p_cache_read, lag(cache_write_tokens) OVER w AS p_cache_write,
           lag(reasoning_tokens) OVER w AS p_reasoning, lag(api_call_count) OVER w AS p_api,
           lag(message_count) OVER w AS p_messages, lag(tool_call_count) OVER w AS p_tools,
           (row_number() OVER w = 1) AS is_first
    FROM observability.hermes_session_samples s
    WINDOW w AS (PARTITION BY session_hash ORDER BY sampled_at)
), base AS (
    SELECT *,
           CASE WHEN is_first AND sampled_at - started_at > interval '5 minutes' THEN false ELSE true END AS known
    FROM ordered
)
SELECT
    sampled_at, session_hash, profile, source, chat_type, model, known,
    CASE WHEN known THEN greatest(input_tokens - COALESCE(p_input, 0), 0) END AS input_tokens,
    CASE WHEN known THEN greatest(output_tokens - COALESCE(p_output, 0), 0) END AS output_tokens,
    CASE WHEN known THEN greatest(cache_read_tokens - COALESCE(p_cache_read, 0), 0) END AS cache_read_tokens,
    CASE WHEN known THEN greatest(cache_write_tokens - COALESCE(p_cache_write, 0), 0) END AS cache_write_tokens,
    CASE WHEN known THEN greatest(reasoning_tokens - COALESCE(p_reasoning, 0), 0) END AS reasoning_tokens,
    CASE WHEN known THEN greatest(api_call_count - COALESCE(p_api, 0), 0) END AS api_calls,
    CASE WHEN known THEN greatest(message_count - COALESCE(p_messages, 0), 0) END AS messages,
    CASE WHEN known THEN greatest(tool_call_count - COALESCE(p_tools, 0), 0) END AS tool_calls
FROM base;

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
        GRANT SELECT ON observability.hermes_session_samples, observability.hermes_tool_events,
            observability.hermes_sessions_current, observability.hermes_usage_deltas TO grafana_reader;
    END IF;
END;
$$;
