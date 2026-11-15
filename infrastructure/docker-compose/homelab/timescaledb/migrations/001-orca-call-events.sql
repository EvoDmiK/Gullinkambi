-- Additive: per-call ORCA usage events. Existing orca_*_daily tables are untouched.
-- Apply with: psql -v ON_ERROR_STOP=1 -v app_user=<role> -f 001-orca-call-events.sql
-- Rollback:   DROP FUNCTION observability.ingest_orca_call_event(jsonb, text, timestamptz);
--             DROP TABLE observability.orca_call_events;
CREATE TABLE IF NOT EXISTS observability.orca_call_events (
    event_key text PRIMARY KEY,
    schema_version integer NOT NULL,
    normalization_version integer NOT NULL,
    agent text NOT NULL CHECK (agent IN ('codex', 'claude')),
    model text NOT NULL,
    origin_namespace text NOT NULL,
    session_id_hash text NOT NULL,
    source_request_id_hash text NOT NULL,
    project_key text NOT NULL,
    origin_evidence text NOT NULL
        CHECK (origin_evidence IN ('managed_metadata', 'explicit_mapping', 'path_only', 'unknown')),
    source_kind text NOT NULL CHECK (source_kind IN ('raw_call', 'cumulative_delta', 'legacy_aggregate')),
    occurred_at timestamptz,
    event_status text NOT NULL CHECK (event_status IN ('partial', 'final', 'unknown')),
    revision bigint NOT NULL,
    -- NULL = unknown (see unknown_fields), never a coerced 0.
    uncached_input_tokens bigint CHECK (uncached_input_tokens >= 0),
    cache_read_tokens bigint CHECK (cache_read_tokens >= 0),
    cache_write_tokens bigint CHECK (cache_write_tokens >= 0),
    output_tokens bigint CHECK (output_tokens >= 0),
    reasoning_tokens_subset bigint CHECK (reasoning_tokens_subset >= 0),
    normalized_input_tokens bigint CHECK (normalized_input_tokens >= 0),
    processed_total_tokens bigint CHECK (processed_total_tokens >= 0),
    non_cache_tokens bigint CHECK (non_cache_tokens >= 0),
    unknown_fields jsonb NOT NULL DEFAULT '{}'::jsonb,
    task_id text,
    attempt_id text,
    attribution_method text,
    attribution_status text NOT NULL DEFAULT 'unassigned'
        CHECK (attribution_status IN ('explicit', 'unassigned', 'ambiguous')),
    collector_id text NOT NULL,
    first_observed_at timestamptz NOT NULL,
    last_observed_at timestamptz NOT NULL
);

CREATE INDEX IF NOT EXISTS orca_call_events_time_idx
    ON observability.orca_call_events (occurred_at DESC);
CREATE INDEX IF NOT EXISTS orca_call_events_session_idx
    ON observability.orca_call_events (session_id_hash, occurred_at DESC);
CREATE INDEX IF NOT EXISTS orca_call_events_attempt_idx
    ON observability.orca_call_events (task_id, attempt_id) WHERE task_id IS NOT NULL;

-- Idempotent upsert. A final revision is never replaced by a partial one, and a
-- final→final correction applies only with a strictly higher revision, so replays
-- and extra source copies cannot flip stored values. Attribution columns are owned
-- by the attribution step and are not touched here.
CREATE OR REPLACE FUNCTION observability.ingest_orca_call_event(
    event jsonb,
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
    IF COALESCE(event->>'event_key', '') = ''
       OR COALESCE(event->>'agent', '') = ''
       OR COALESCE(event->>'model', '') = ''
       OR COALESCE(event->>'origin_namespace', '') = ''
       OR COALESCE(event->>'session_id_hash', '') = ''
       OR COALESCE(event->>'source_request_id_hash', '') = ''
       OR COALESCE(event->>'project_key', '') = '' THEN
        RAISE EXCEPTION 'invalid Orca call event payload';
    END IF;

    INSERT INTO observability.orca_call_events AS current (
        event_key, schema_version, normalization_version, agent, model,
        origin_namespace, session_id_hash, source_request_id_hash, project_key,
        origin_evidence, source_kind, occurred_at, event_status, revision,
        uncached_input_tokens, cache_read_tokens, cache_write_tokens, output_tokens,
        reasoning_tokens_subset, normalized_input_tokens, processed_total_tokens,
        non_cache_tokens, unknown_fields, collector_id, first_observed_at, last_observed_at
    ) VALUES (
        event->>'event_key', (event->>'schema_version')::integer,
        (event->>'normalization_version')::integer, event->>'agent', event->>'model',
        event->>'origin_namespace', event->>'session_id_hash',
        event->>'source_request_id_hash', event->>'project_key',
        COALESCE(event->>'origin_evidence', 'unknown'),
        COALESCE(event->>'source_kind', 'raw_call'),
        (event->>'occurred_at')::timestamptz,
        COALESCE(event->>'event_status', 'unknown'),
        COALESCE((event->>'revision')::bigint, 0),
        (event->>'uncached_input_tokens')::bigint, (event->>'cache_read_tokens')::bigint,
        (event->>'cache_write_tokens')::bigint, (event->>'output_tokens')::bigint,
        (event->>'reasoning_tokens_subset')::bigint,
        (event->>'normalized_input_tokens')::bigint,
        (event->>'processed_total_tokens')::bigint, (event->>'non_cache_tokens')::bigint,
        COALESCE(event->'unknown_fields', '{}'::jsonb),
        incoming_collector_id, observed_at, observed_at
    )
    ON CONFLICT (event_key) DO UPDATE SET
        schema_version = EXCLUDED.schema_version,
        normalization_version = EXCLUDED.normalization_version,
        model = EXCLUDED.model,
        occurred_at = EXCLUDED.occurred_at,
        event_status = EXCLUDED.event_status,
        revision = EXCLUDED.revision,
        uncached_input_tokens = EXCLUDED.uncached_input_tokens,
        cache_read_tokens = EXCLUDED.cache_read_tokens,
        cache_write_tokens = EXCLUDED.cache_write_tokens,
        output_tokens = EXCLUDED.output_tokens,
        reasoning_tokens_subset = EXCLUDED.reasoning_tokens_subset,
        normalized_input_tokens = EXCLUDED.normalized_input_tokens,
        processed_total_tokens = EXCLUDED.processed_total_tokens,
        non_cache_tokens = EXCLUDED.non_cache_tokens,
        unknown_fields = EXCLUDED.unknown_fields,
        collector_id = EXCLUDED.collector_id,
        last_observed_at = EXCLUDED.last_observed_at
    WHERE (current.event_status <> 'final' AND EXCLUDED.event_status = 'final')
       OR (current.event_status = EXCLUDED.event_status AND EXCLUDED.revision > current.revision)
       OR (current.event_status = 'unknown');
    GET DIAGNOSTICS changed_rows = ROW_COUNT;
    RETURN changed_rows > 0;
END;
$$;

REVOKE ALL ON FUNCTION observability.ingest_orca_call_event(jsonb, text, timestamptz) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION observability.ingest_orca_call_event(jsonb, text, timestamptz) TO :"app_user";

DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'grafana_reader') THEN
        GRANT SELECT ON observability.orca_call_events TO grafana_reader;
    END IF;
END;
$$;
