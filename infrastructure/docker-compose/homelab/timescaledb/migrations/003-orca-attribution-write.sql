-- Additive: least-privilege write path for task/attempt attribution.
-- Apply with: psql -v ON_ERROR_STOP=1 -v app_user=<role> -f 003-orca-attribution-write.sql
-- Rollback:   DROP FUNCTION observability.assign_orca_call_attributions(jsonb);
--             REVOKE SELECT ON observability.orca_call_events FROM <app_user>;
CREATE OR REPLACE FUNCTION observability.assign_orca_call_attributions(items jsonb)
RETURNS integer
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = observability, pg_temp
AS $$
DECLARE
    changed integer;
BEGIN
    WITH incoming AS (
        SELECT i->>'event_key' AS event_key, i->>'task_id' AS task_id, i->>'attempt_id' AS attempt_id,
               i->>'attribution_method' AS method, i->>'attribution_status' AS status
        FROM jsonb_array_elements(items) AS i
    ), updated AS (
        UPDATE observability.orca_call_events e
        SET task_id = incoming.task_id, attempt_id = incoming.attempt_id,
            attribution_method = incoming.method, attribution_status = incoming.status
        FROM incoming
        WHERE e.event_key = incoming.event_key
          AND (e.task_id, e.attempt_id, e.attribution_method, e.attribution_status)
              IS DISTINCT FROM (incoming.task_id, incoming.attempt_id, incoming.method, incoming.status)
        RETURNING 1
    )
    SELECT count(*) INTO changed FROM updated;
    RETURN changed;
END;
$$;

REVOKE ALL ON FUNCTION observability.assign_orca_call_attributions(jsonb) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION observability.assign_orca_call_attributions(jsonb) TO :"app_user";
-- Event rows hold hashed identifiers and token counts only; the pipeline needs to read them.
GRANT USAGE ON SCHEMA observability TO :"app_user";
GRANT SELECT ON observability.orca_call_events TO :"app_user";
