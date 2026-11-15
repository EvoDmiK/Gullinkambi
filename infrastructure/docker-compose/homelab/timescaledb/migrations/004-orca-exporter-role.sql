-- Additive: least-privilege role for the MLflow exporter (no hermes_* / orca_*_daily access).
-- Apply as the Timescale superuser, passing the password via the environment (not argv):
--   ORCA_EXPORTER_PASSWORD=... psql -v ON_ERROR_STOP=1 -v exporter_user=orca_exporter -f 004-orca-exporter-role.sql
-- Rollback: REVOKE ALL ON observability.orca_attempt_snapshots, observability.orca_mlflow_outbox,
--           observability.orca_mlflow_runs FROM orca_exporter; REVOKE USAGE ON SCHEMA observability FROM orca_exporter;
--           DROP ROLE orca_exporter;
\getenv exporter_password ORCA_EXPORTER_PASSWORD
SELECT set_config('orca.exporter_user', :'exporter_user', false) \g /dev/null
SELECT set_config('orca.exporter_password', :'exporter_password', false) \g /dev/null

DO $$
BEGIN
    IF current_setting('orca.exporter_password', true) = '' THEN
        RAISE EXCEPTION 'ORCA_EXPORTER_PASSWORD is empty';
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = current_setting('orca.exporter_user')) THEN
        EXECUTE format('CREATE ROLE %I WITH LOGIN PASSWORD %L',
            current_setting('orca.exporter_user'), current_setting('orca.exporter_password'));
    END IF;
END;
$$;

GRANT USAGE ON SCHEMA observability TO :"exporter_user";
GRANT SELECT ON observability.orca_attempt_snapshots TO :"exporter_user";
GRANT SELECT, UPDATE ON observability.orca_mlflow_outbox TO :"exporter_user";
GRANT SELECT, INSERT, UPDATE ON observability.orca_mlflow_runs TO :"exporter_user";
