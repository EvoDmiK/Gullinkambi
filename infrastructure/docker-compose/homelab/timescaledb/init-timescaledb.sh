#!/usr/bin/env bash
set -euo pipefail

psql -v ON_ERROR_STOP=1 \
  --username "$POSTGRES_USER" \
  --dbname "$POSTGRES_DB" \
  --set=app_user="$TIMESCALE_APP_USER" \
  --set=app_password="$TIMESCALE_APP_PASSWORD" \
  --set=backup_user="$TIMESCALE_BACKUP_USER" \
  --set=backup_password="$TIMESCALE_BACKUP_PASSWORD" <<'SQL'
CREATE EXTENSION IF NOT EXISTS timescaledb;
SELECT set_config('timescaledb.app_user', :'app_user', false) \g /dev/null
SELECT set_config('timescaledb.app_password', :'app_password', false) \g /dev/null
SELECT set_config('timescaledb.backup_user', :'backup_user', false) \g /dev/null
SELECT set_config('timescaledb.backup_password', :'backup_password', false) \g /dev/null

DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1
    FROM pg_catalog.pg_roles
    WHERE rolname = current_setting('timescaledb.app_user', true)
  ) THEN
    EXECUTE format(
      'CREATE ROLE %I WITH LOGIN PASSWORD %L',
      current_setting('timescaledb.app_user', true),
      current_setting('timescaledb.app_password', true)
    );
  END IF;

  IF NOT EXISTS (
    SELECT 1
    FROM pg_catalog.pg_roles
    WHERE rolname = current_setting('timescaledb.backup_user', true)
  ) THEN
    EXECUTE format(
      'CREATE ROLE %I WITH LOGIN PASSWORD %L',
      current_setting('timescaledb.backup_user', true),
      current_setting('timescaledb.backup_password', true)
    );
  END IF;
END
$$;
SQL

psql -v ON_ERROR_STOP=1 \
  --username "$POSTGRES_USER" \
  --dbname "$POSTGRES_DB" \
  --set=app_user="$TIMESCALE_APP_USER" \
  --set=backup_user="$TIMESCALE_BACKUP_USER" <<'SQL'
SELECT set_config('timescaledb.app_user', :'app_user', false) \g /dev/null
SELECT set_config('timescaledb.backup_user', :'backup_user', false) \g /dev/null

DO $$
BEGIN
  EXECUTE format('GRANT CONNECT, CREATE ON DATABASE %I TO %I', current_database(), current_setting('timescaledb.app_user'));
  EXECUTE format('GRANT USAGE, CREATE ON SCHEMA public TO %I', current_setting('timescaledb.app_user'));
  EXECUTE format('GRANT ALL PRIVILEGES ON ALL TABLES IN SCHEMA public TO %I', current_setting('timescaledb.app_user'));
  EXECUTE format('GRANT ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA public TO %I', current_setting('timescaledb.app_user'));
  EXECUTE format('ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON TABLES TO %I', current_setting('timescaledb.app_user'));
  EXECUTE format('ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON SEQUENCES TO %I', current_setting('timescaledb.app_user'));

  EXECUTE format('GRANT CONNECT ON DATABASE %I TO %I', current_database(), current_setting('timescaledb.backup_user'));
  EXECUTE format('GRANT USAGE ON SCHEMA public TO %I', current_setting('timescaledb.backup_user'));
  EXECUTE format('GRANT SELECT ON ALL TABLES IN SCHEMA public TO %I', current_setting('timescaledb.backup_user'));
  EXECUTE format('GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO %I', current_setting('timescaledb.backup_user'));
  EXECUTE format('ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT ON TABLES TO %I', current_setting('timescaledb.backup_user'));
  EXECUTE format('ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT USAGE, SELECT ON SEQUENCES TO %I', current_setting('timescaledb.backup_user'));
  EXECUTE format('GRANT pg_read_all_data TO %I', current_setting('timescaledb.backup_user'));
END
$$;
SQL
