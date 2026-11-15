-- Additive: tool-call counts per model request (names and counts only, never arguments or results).
-- NULL means "not observed" (rows stored before this migration, or a provider that does not expose it);
-- an empty object {} means "observed, no tool used". tool_failures is only filled for providers that report it.
-- Apply with: psql -v ON_ERROR_STOP=1 -f 008-orca-tool-calls.sql
-- Rollback:   ALTER TABLE observability.orca_call_events DROP COLUMN tool_calls, DROP COLUMN tool_calls_total,
--             DROP COLUMN tool_failures;  then re-run migration 001 to restore the previous function.
ALTER TABLE observability.orca_call_events
    ADD COLUMN IF NOT EXISTS tool_calls jsonb,
    ADD COLUMN IF NOT EXISTS tool_calls_total integer CHECK (tool_calls_total >= 0),
    ADD COLUMN IF NOT EXISTS tool_failures integer CHECK (tool_failures >= 0);

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
    IF event ? 'tool_calls' AND jsonb_typeof(event->'tool_calls') NOT IN ('object', 'null')
       OR length(COALESCE(event->>'tool_calls', '')) > 4096 THEN
        RAISE EXCEPTION 'invalid Orca call event payload';
    END IF;

    INSERT INTO observability.orca_call_events AS current (
        event_key, schema_version, normalization_version, agent, model,
        origin_namespace, session_id_hash, source_request_id_hash, project_key,
        origin_evidence, source_kind, occurred_at, event_status, revision,
        uncached_input_tokens, cache_read_tokens, cache_write_tokens, output_tokens,
        reasoning_tokens_subset, normalized_input_tokens, processed_total_tokens,
        non_cache_tokens, unknown_fields, tool_calls, tool_calls_total, tool_failures,
        collector_id, first_observed_at, last_observed_at
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
        NULLIF(event->'tool_calls', 'null'::jsonb), (event->>'tool_calls_total')::integer, (event->>'tool_failures')::integer,
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
        tool_calls = EXCLUDED.tool_calls,
        tool_calls_total = EXCLUDED.tool_calls_total,
        tool_failures = EXCLUDED.tool_failures,
        collector_id = EXCLUDED.collector_id,
        last_observed_at = EXCLUDED.last_observed_at
    WHERE (current.event_status <> 'final' AND EXCLUDED.event_status = 'final')
       OR (current.event_status = EXCLUDED.event_status AND EXCLUDED.revision > current.revision)
       OR (current.event_status = 'unknown')
       -- Backfill: rows stored before migration 008 have tool_calls NULL ("not observed"). The same revision
       -- may fill them in, but a partial update never replaces a final row.
       OR (current.tool_calls IS NULL AND EXCLUDED.tool_calls IS NOT NULL
           AND NOT (current.event_status = 'final' AND EXCLUDED.event_status <> 'final'));
    GET DIAGNOSTICS changed_rows = ROW_COUNT;
    RETURN changed_rows > 0;
END;
$$;
