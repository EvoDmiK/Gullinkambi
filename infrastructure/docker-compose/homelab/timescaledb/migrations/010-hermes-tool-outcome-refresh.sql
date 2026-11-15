-- Additive: a tool event may now refresh its outcome. Classification rules improved, so a stored
-- 'unknown' outcome can be replaced by a real one; a known outcome is never overwritten.
-- Apply with: psql -v ON_ERROR_STOP=1 -v app_user=<role> -f 010-hermes-tool-outcome-refresh.sql
-- Rollback:   re-run 009-hermes-sessions.sql (it re-creates the insert-only version of the function).
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

    INSERT INTO observability.hermes_tool_events AS current (
        event_key, schema_version, profile, session_hash, occurred_at, tool, outcome, collector_id, first_observed_at
    ) VALUES (
        item->>'event_key', COALESCE((item->>'schema_version')::integer, 1), item->>'profile',
        item->>'session_hash', (item->>'occurred_at')::timestamptz, left(item->>'tool', 64),
        COALESCE(item->>'outcome', 'unknown'), incoming_collector_id, observed_at
    )
    ON CONFLICT (event_key) DO UPDATE SET outcome = EXCLUDED.outcome
    WHERE current.outcome = 'unknown' AND EXCLUDED.outcome <> 'unknown';
    GET DIAGNOSTICS changed_rows = ROW_COUNT;
    RETURN changed_rows > 0;
END;
$$;

REVOKE ALL ON FUNCTION observability.ingest_hermes_tool_event(jsonb, text, timestamptz) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION observability.ingest_hermes_tool_event(jsonb, text, timestamptz) TO :"app_user";
