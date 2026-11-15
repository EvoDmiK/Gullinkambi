-- Additive: allow calls to be grouped automatically per session when nobody registered a task.
-- attribution_status gains 'auto' (attribution_method = 'auto_session'); explicit manifests still win,
-- and 'ambiguous' calls are never auto-grouped.
-- Apply with: psql -v ON_ERROR_STOP=1 -f 007-orca-auto-attribution.sql
-- Rollback:   UPDATE observability.orca_call_events SET task_id=NULL, attempt_id=NULL, attribution_method=NULL,
--               attribution_status='unassigned' WHERE attribution_status='auto';
--             then re-create the constraint without 'auto'.
ALTER TABLE observability.orca_call_events
    DROP CONSTRAINT IF EXISTS orca_call_events_attribution_status_check;
ALTER TABLE observability.orca_call_events
    ADD CONSTRAINT orca_call_events_attribution_status_check
    CHECK (attribution_status IN ('explicit', 'unassigned', 'ambiguous', 'auto'));
