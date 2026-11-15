#!/usr/bin/env bash
set -euo pipefail

psql -v ON_ERROR_STOP=1 \
  --username "$POSTGRES_USER" \
  --dbname "$POSTGRES_DB" \
  --set=app_user="$TIMESCALE_APP_USER" \
  --set=backup_user="$TIMESCALE_BACKUP_USER" <<'SQL'
CREATE EXTENSION IF NOT EXISTS timescaledb;
CREATE SCHEMA IF NOT EXISTS observability;

CREATE TABLE IF NOT EXISTS observability.hermes_runs (
    event_id text PRIMARY KEY,
    session_id text NOT NULL,
    profile text NOT NULL,
    source text NOT NULL,
    status text NOT NULL,
    end_reason text,
    started_at timestamptz NOT NULL,
    ended_at timestamptz,
    duration_ms bigint,
    model text NOT NULL,
    provider text NOT NULL,
    billing_mode text,
    message_count integer NOT NULL DEFAULT 0,
    tool_call_count integer NOT NULL DEFAULT 0,
    api_call_count integer NOT NULL DEFAULT 0,
    input_tokens bigint NOT NULL DEFAULT 0,
    output_tokens bigint NOT NULL DEFAULT 0,
    cache_read_tokens bigint NOT NULL DEFAULT 0,
    cache_write_tokens bigint NOT NULL DEFAULT 0,
    reasoning_tokens bigint NOT NULL DEFAULT 0,
    estimated_cost_usd double precision NOT NULL DEFAULT 0,
    actual_cost_usd double precision NOT NULL DEFAULT 0,
    cost_status text,
    pricing_version text,
    rewind_count integer NOT NULL DEFAULT 0,
    parent_session_id text,
    tool_usage jsonb NOT NULL DEFAULT '{}'::jsonb,
    fingerprint text NOT NULL,
    collector_id text NOT NULL,
    first_observed_at timestamptz NOT NULL,
    last_observed_at timestamptz NOT NULL
);

CREATE INDEX IF NOT EXISTS hermes_runs_profile_started_idx
    ON observability.hermes_runs (profile, started_at DESC);
CREATE INDEX IF NOT EXISTS hermes_runs_status_started_idx
    ON observability.hermes_runs (status, started_at DESC);

CREATE TABLE IF NOT EXISTS observability.orca_usage_daily (
    event_id text PRIMARY KEY,
    usage_date date NOT NULL,
    agent text NOT NULL,
    model text NOT NULL,
    project_label text NOT NULL,
    project_hash text NOT NULL,
    request_count bigint NOT NULL DEFAULT 0,
    input_tokens bigint NOT NULL DEFAULT 0,
    cache_read_tokens bigint NOT NULL DEFAULT 0,
    cache_write_tokens bigint NOT NULL DEFAULT 0,
    output_tokens bigint NOT NULL DEFAULT 0,
    reasoning_tokens bigint NOT NULL DEFAULT 0,
    total_tokens bigint NOT NULL DEFAULT 0,
    fingerprint text NOT NULL,
    collector_id text NOT NULL,
    first_observed_at timestamptz NOT NULL,
    last_observed_at timestamptz NOT NULL
);

CREATE INDEX IF NOT EXISTS orca_usage_daily_date_idx
    ON observability.orca_usage_daily (usage_date DESC);
CREATE INDEX IF NOT EXISTS orca_usage_daily_agent_model_idx
    ON observability.orca_usage_daily (agent, model, usage_date DESC);

CREATE TABLE IF NOT EXISTS observability.orca_thread_usage_daily (
    event_id text PRIMARY KEY,
    thread_id text NOT NULL,
    thread_name text,
    skill_names jsonb NOT NULL DEFAULT '[]'::jsonb,
    usage_date date NOT NULL,
    agent text NOT NULL,
    model text NOT NULL,
    project_label text NOT NULL,
    project_hash text NOT NULL,
    request_count bigint NOT NULL DEFAULT 0,
    input_tokens bigint NOT NULL DEFAULT 0,
    cache_read_tokens bigint NOT NULL DEFAULT 0,
    cache_write_tokens bigint NOT NULL DEFAULT 0,
    output_tokens bigint NOT NULL DEFAULT 0,
    reasoning_tokens bigint NOT NULL DEFAULT 0,
    total_tokens bigint NOT NULL DEFAULT 0,
    fingerprint text NOT NULL,
    collector_id text NOT NULL,
    first_observed_at timestamptz NOT NULL,
    last_observed_at timestamptz NOT NULL
);

ALTER TABLE observability.orca_thread_usage_daily
    ADD COLUMN IF NOT EXISTS thread_name text;
ALTER TABLE observability.orca_thread_usage_daily
    ADD COLUMN IF NOT EXISTS skill_names jsonb NOT NULL DEFAULT '[]'::jsonb;

CREATE INDEX IF NOT EXISTS orca_thread_usage_daily_date_idx
    ON observability.orca_thread_usage_daily (usage_date DESC);
CREATE INDEX IF NOT EXISTS orca_thread_usage_daily_thread_idx
    ON observability.orca_thread_usage_daily (thread_id, usage_date DESC);

CREATE TABLE IF NOT EXISTS observability.hermes_goal_runs (
    event_id text PRIMARY KEY,
    session_id text NOT NULL,
    profile text NOT NULL,
    model text NOT NULL,
    status text NOT NULL,
    turns_used integer NOT NULL DEFAULT 0,
    max_turns integer NOT NULL DEFAULT 0,
    started_at timestamptz NOT NULL,
    ended_at timestamptz,
    duration_ms bigint,
    last_verdict text,
    paused_reason text,
    fingerprint text NOT NULL,
    collector_id text NOT NULL,
    first_observed_at timestamptz NOT NULL,
    last_observed_at timestamptz NOT NULL
);

CREATE INDEX IF NOT EXISTS hermes_goal_runs_profile_started_idx
    ON observability.hermes_goal_runs (profile, started_at DESC);
CREATE INDEX IF NOT EXISTS hermes_goal_runs_model_started_idx
    ON observability.hermes_goal_runs (model, started_at DESC);
CREATE INDEX IF NOT EXISTS hermes_goal_runs_status_started_idx
    ON observability.hermes_goal_runs (status, started_at DESC);

CREATE TABLE IF NOT EXISTS observability.hermes_run_events (
    time timestamptz NOT NULL,
    event_id text NOT NULL,
    session_id text NOT NULL,
    profile text NOT NULL,
    source text NOT NULL,
    status text NOT NULL,
    end_reason text,
    started_at timestamptz NOT NULL,
    ended_at timestamptz,
    duration_ms bigint,
    model text NOT NULL,
    provider text NOT NULL,
    input_tokens bigint NOT NULL DEFAULT 0,
    output_tokens bigint NOT NULL DEFAULT 0,
    reasoning_tokens bigint NOT NULL DEFAULT 0,
    estimated_cost_usd double precision NOT NULL DEFAULT 0,
    actual_cost_usd double precision NOT NULL DEFAULT 0,
    tool_call_count integer NOT NULL DEFAULT 0,
    tool_usage jsonb NOT NULL DEFAULT '{}'::jsonb,
    fingerprint text NOT NULL,
    collector_id text NOT NULL,
    PRIMARY KEY (time, event_id, fingerprint)
);

SELECT create_hypertable(
    'observability.hermes_run_events',
    by_range('time'),
    if_not_exists => TRUE
);

CREATE INDEX IF NOT EXISTS hermes_run_events_profile_time_idx
    ON observability.hermes_run_events (profile, time DESC);
CREATE INDEX IF NOT EXISTS hermes_run_events_status_time_idx
    ON observability.hermes_run_events (status, time DESC);

CREATE TABLE IF NOT EXISTS observability.hermes_kanban_tasks (
    event_id text PRIMARY KEY,
    board text NOT NULL,
    task_id text NOT NULL,
    title text,
    title_hash text,
    assignee text,
    status text NOT NULL,
    priority integer NOT NULL DEFAULT 0,
    created_by text,
    created_at timestamptz,
    started_at timestamptz,
    completed_at timestamptz,
    workspace_kind text,
    workspace_present boolean NOT NULL DEFAULT false,
    project_id text,
    tenant text,
    result_present boolean NOT NULL DEFAULT false,
    idempotency_key text,
    consecutive_failures integer NOT NULL DEFAULT 0,
    worker_pid_present boolean NOT NULL DEFAULT false,
    last_failure_present boolean NOT NULL DEFAULT false,
    max_runtime_seconds integer,
    last_heartbeat_at timestamptz,
    current_run_id bigint,
    workflow_template_id text,
    current_step_key text,
    skill_count integer NOT NULL DEFAULT 0,
    model_override text,
    max_retries integer,
    goal_mode boolean NOT NULL DEFAULT false,
    goal_max_turns integer,
    session_id text,
    block_kind text,
    block_recurrences integer NOT NULL DEFAULT 0,
    parent_count integer NOT NULL DEFAULT 0,
    child_count integer NOT NULL DEFAULT 0,
    comment_count integer NOT NULL DEFAULT 0,
    attachment_count integer NOT NULL DEFAULT 0,
    run_count integer NOT NULL DEFAULT 0,
    latest_run_id bigint,
    latest_run_status text,
    latest_run_outcome text,
    latest_run_started_at timestamptz,
    latest_run_ended_at timestamptz,
    fingerprint text NOT NULL,
    collector_id text NOT NULL,
    first_observed_at timestamptz NOT NULL,
    last_observed_at timestamptz NOT NULL,
    UNIQUE (board, task_id)
);

CREATE INDEX IF NOT EXISTS hermes_kanban_tasks_board_status_idx
    ON observability.hermes_kanban_tasks (board, status);
CREATE INDEX IF NOT EXISTS hermes_kanban_tasks_assignee_status_idx
    ON observability.hermes_kanban_tasks (assignee, status);
CREATE INDEX IF NOT EXISTS hermes_kanban_tasks_created_idx
    ON observability.hermes_kanban_tasks (created_at DESC);
CREATE INDEX IF NOT EXISTS hermes_kanban_tasks_heartbeat_idx
    ON observability.hermes_kanban_tasks (last_heartbeat_at DESC);

CREATE TABLE IF NOT EXISTS observability.hermes_kanban_task_runs (
    event_id text PRIMARY KEY,
    board text NOT NULL,
    run_id bigint NOT NULL,
    task_id text NOT NULL,
    profile text,
    step_key text,
    status text NOT NULL,
    claim_lock_present boolean NOT NULL DEFAULT false,
    claim_expires_at timestamptz,
    worker_pid_present boolean NOT NULL DEFAULT false,
    max_runtime_seconds integer,
    last_heartbeat_at timestamptz,
    started_at timestamptz,
    ended_at timestamptz,
    outcome text,
    summary_present boolean NOT NULL DEFAULT false,
    metadata_keys jsonb NOT NULL DEFAULT '[]'::jsonb,
    error_present boolean NOT NULL DEFAULT false,
    fingerprint text NOT NULL,
    collector_id text NOT NULL,
    first_observed_at timestamptz NOT NULL,
    last_observed_at timestamptz NOT NULL,
    UNIQUE (board, run_id)
);

CREATE INDEX IF NOT EXISTS hermes_kanban_task_runs_task_idx
    ON observability.hermes_kanban_task_runs (board, task_id, started_at DESC);
CREATE INDEX IF NOT EXISTS hermes_kanban_task_runs_status_idx
    ON observability.hermes_kanban_task_runs (status, started_at DESC);

CREATE TABLE IF NOT EXISTS observability.hermes_kanban_task_events (
    event_id text PRIMARY KEY,
    board text NOT NULL,
    source_event_id bigint NOT NULL,
    task_id text NOT NULL,
    run_id bigint,
    kind text NOT NULL,
    payload_present boolean NOT NULL DEFAULT false,
    payload_keys jsonb NOT NULL DEFAULT '[]'::jsonb,
    created_at timestamptz,
    fingerprint text NOT NULL,
    collector_id text NOT NULL,
    first_observed_at timestamptz NOT NULL,
    last_observed_at timestamptz NOT NULL,
    UNIQUE (board, source_event_id)
);

CREATE INDEX IF NOT EXISTS hermes_kanban_task_events_task_idx
    ON observability.hermes_kanban_task_events (board, task_id, created_at DESC);
CREATE INDEX IF NOT EXISTS hermes_kanban_task_events_kind_idx
    ON observability.hermes_kanban_task_events (kind, created_at DESC);

CREATE TABLE IF NOT EXISTS observability.hermes_kanban_task_links (
    event_id text PRIMARY KEY,
    board text NOT NULL,
    parent_id text NOT NULL,
    child_id text NOT NULL,
    fingerprint text NOT NULL,
    collector_id text NOT NULL,
    first_observed_at timestamptz NOT NULL,
    last_observed_at timestamptz NOT NULL,
    UNIQUE (board, parent_id, child_id)
);

CREATE INDEX IF NOT EXISTS hermes_kanban_task_links_parent_idx
    ON observability.hermes_kanban_task_links (board, parent_id);
CREATE INDEX IF NOT EXISTS hermes_kanban_task_links_child_idx
    ON observability.hermes_kanban_task_links (board, child_id);

CREATE OR REPLACE FUNCTION observability.ingest_hermes_run(
    run jsonb,
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
    IF COALESCE(run->>'event_id', '') = ''
       OR COALESCE(run->>'session_id', '') = ''
       OR COALESCE(run->>'profile', '') = ''
       OR COALESCE(run->>'started_at', '') = ''
       OR COALESCE(run->>'fingerprint', '') = '' THEN
        RAISE EXCEPTION 'invalid Hermes telemetry run payload';
    END IF;

    INSERT INTO observability.hermes_runs AS current (
        event_id, session_id, profile, source, status, end_reason,
        started_at, ended_at, duration_ms, model, provider, billing_mode,
        message_count, tool_call_count, api_call_count,
        input_tokens, output_tokens, cache_read_tokens, cache_write_tokens,
        reasoning_tokens, estimated_cost_usd, actual_cost_usd, cost_status,
        pricing_version, rewind_count, parent_session_id, tool_usage,
        fingerprint, collector_id, first_observed_at, last_observed_at
    ) VALUES (
        run->>'event_id', run->>'session_id', run->>'profile',
        COALESCE(run->>'source', 'unknown'), COALESCE(run->>'status', 'unknown'),
        NULLIF(run->>'end_reason', ''), (run->>'started_at')::timestamptz,
        NULLIF(run->>'ended_at', '')::timestamptz,
        NULLIF(run->>'duration_ms', '')::bigint,
        COALESCE(run->>'model', 'unknown'), COALESCE(run->>'provider', 'unknown'),
        NULLIF(run->>'billing_mode', ''),
        COALESCE((run->>'message_count')::integer, 0),
        COALESCE((run->>'tool_call_count')::integer, 0),
        COALESCE((run->>'api_call_count')::integer, 0),
        COALESCE((run->>'input_tokens')::bigint, 0),
        COALESCE((run->>'output_tokens')::bigint, 0),
        COALESCE((run->>'cache_read_tokens')::bigint, 0),
        COALESCE((run->>'cache_write_tokens')::bigint, 0),
        COALESCE((run->>'reasoning_tokens')::bigint, 0),
        COALESCE((run->>'estimated_cost_usd')::double precision, 0),
        COALESCE((run->>'actual_cost_usd')::double precision, 0),
        NULLIF(run->>'cost_status', ''), NULLIF(run->>'pricing_version', ''),
        COALESCE((run->>'rewind_count')::integer, 0),
        NULLIF(run->>'parent_session_id', ''),
        COALESCE(run->'tool_usage', '{}'::jsonb), run->>'fingerprint',
        incoming_collector_id, observed_at, observed_at
    )
    ON CONFLICT (event_id) DO UPDATE SET
        session_id = EXCLUDED.session_id,
        profile = EXCLUDED.profile,
        source = EXCLUDED.source,
        status = EXCLUDED.status,
        end_reason = EXCLUDED.end_reason,
        started_at = EXCLUDED.started_at,
        ended_at = EXCLUDED.ended_at,
        duration_ms = EXCLUDED.duration_ms,
        model = EXCLUDED.model,
        provider = EXCLUDED.provider,
        billing_mode = EXCLUDED.billing_mode,
        message_count = EXCLUDED.message_count,
        tool_call_count = EXCLUDED.tool_call_count,
        api_call_count = EXCLUDED.api_call_count,
        input_tokens = EXCLUDED.input_tokens,
        output_tokens = EXCLUDED.output_tokens,
        cache_read_tokens = EXCLUDED.cache_read_tokens,
        cache_write_tokens = EXCLUDED.cache_write_tokens,
        reasoning_tokens = EXCLUDED.reasoning_tokens,
        estimated_cost_usd = EXCLUDED.estimated_cost_usd,
        actual_cost_usd = EXCLUDED.actual_cost_usd,
        cost_status = EXCLUDED.cost_status,
        pricing_version = EXCLUDED.pricing_version,
        rewind_count = EXCLUDED.rewind_count,
        parent_session_id = EXCLUDED.parent_session_id,
        tool_usage = EXCLUDED.tool_usage,
        fingerprint = EXCLUDED.fingerprint,
        collector_id = EXCLUDED.collector_id,
        last_observed_at = EXCLUDED.last_observed_at
    WHERE current.fingerprint IS DISTINCT FROM EXCLUDED.fingerprint;

    GET DIAGNOSTICS changed_rows = ROW_COUNT;
    IF changed_rows = 0 THEN
        RETURN FALSE;
    END IF;

    INSERT INTO observability.hermes_run_events (
        time, event_id, session_id, profile, source, status, end_reason,
        started_at, ended_at, duration_ms, model, provider,
        input_tokens, output_tokens, reasoning_tokens,
        estimated_cost_usd, actual_cost_usd, tool_call_count, tool_usage,
        fingerprint, collector_id
    ) VALUES (
        observed_at, run->>'event_id', run->>'session_id', run->>'profile',
        COALESCE(run->>'source', 'unknown'), COALESCE(run->>'status', 'unknown'),
        NULLIF(run->>'end_reason', ''), (run->>'started_at')::timestamptz,
        NULLIF(run->>'ended_at', '')::timestamptz,
        NULLIF(run->>'duration_ms', '')::bigint,
        COALESCE(run->>'model', 'unknown'), COALESCE(run->>'provider', 'unknown'),
        COALESCE((run->>'input_tokens')::bigint, 0),
        COALESCE((run->>'output_tokens')::bigint, 0),
        COALESCE((run->>'reasoning_tokens')::bigint, 0),
        COALESCE((run->>'estimated_cost_usd')::double precision, 0),
        COALESCE((run->>'actual_cost_usd')::double precision, 0),
        COALESCE((run->>'tool_call_count')::integer, 0),
        COALESCE(run->'tool_usage', '{}'::jsonb), run->>'fingerprint',
        incoming_collector_id
    );
    RETURN TRUE;
END;
$$;

REVOKE ALL ON FUNCTION observability.ingest_hermes_run(jsonb, text, timestamptz) FROM PUBLIC;
GRANT USAGE ON SCHEMA observability TO :"app_user";
GRANT EXECUTE ON FUNCTION observability.ingest_hermes_run(jsonb, text, timestamptz) TO :"app_user";

CREATE OR REPLACE FUNCTION observability.ingest_orca_usage(
    usage jsonb,
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
    IF COALESCE(usage->>'event_id', '') = ''
       OR COALESCE(usage->>'usage_date', '') = ''
       OR COALESCE(usage->>'agent', '') = ''
       OR COALESCE(usage->>'model', '') = ''
       OR COALESCE(usage->>'project_hash', '') = ''
       OR COALESCE(usage->>'fingerprint', '') = '' THEN
        RAISE EXCEPTION 'invalid Orca usage payload';
    END IF;

    INSERT INTO observability.orca_usage_daily AS current (
        event_id, usage_date, agent, model, project_label, project_hash,
        request_count, input_tokens, cache_read_tokens, cache_write_tokens,
        output_tokens, reasoning_tokens, total_tokens, fingerprint,
        collector_id, first_observed_at, last_observed_at
    ) VALUES (
        usage->>'event_id', (usage->>'usage_date')::date, usage->>'agent',
        usage->>'model', COALESCE(usage->>'project_label', 'unknown'),
        usage->>'project_hash', COALESCE((usage->>'request_count')::bigint, 0),
        COALESCE((usage->>'input_tokens')::bigint, 0),
        COALESCE((usage->>'cache_read_tokens')::bigint, 0),
        COALESCE((usage->>'cache_write_tokens')::bigint, 0),
        COALESCE((usage->>'output_tokens')::bigint, 0),
        COALESCE((usage->>'reasoning_tokens')::bigint, 0),
        COALESCE((usage->>'total_tokens')::bigint, 0), usage->>'fingerprint',
        incoming_collector_id, observed_at, observed_at
    )
    ON CONFLICT (event_id) DO UPDATE SET
        usage_date = EXCLUDED.usage_date,
        agent = EXCLUDED.agent,
        model = EXCLUDED.model,
        project_label = EXCLUDED.project_label,
        project_hash = EXCLUDED.project_hash,
        request_count = EXCLUDED.request_count,
        input_tokens = EXCLUDED.input_tokens,
        cache_read_tokens = EXCLUDED.cache_read_tokens,
        cache_write_tokens = EXCLUDED.cache_write_tokens,
        output_tokens = EXCLUDED.output_tokens,
        reasoning_tokens = EXCLUDED.reasoning_tokens,
        total_tokens = EXCLUDED.total_tokens,
        fingerprint = EXCLUDED.fingerprint,
        collector_id = EXCLUDED.collector_id,
        last_observed_at = EXCLUDED.last_observed_at
    WHERE current.fingerprint IS DISTINCT FROM EXCLUDED.fingerprint;

    GET DIAGNOSTICS changed_rows = ROW_COUNT;
    RETURN changed_rows > 0;
END;
$$;

REVOKE ALL ON FUNCTION observability.ingest_orca_usage(jsonb, text, timestamptz) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION observability.ingest_orca_usage(jsonb, text, timestamptz) TO :"app_user";

CREATE OR REPLACE FUNCTION observability.ingest_orca_thread_usage(
    usage jsonb,
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
    IF COALESCE(usage->>'event_id', '') = ''
       OR COALESCE(usage->>'thread_id', '') = ''
       OR COALESCE(usage->>'usage_date', '') = ''
       OR COALESCE(usage->>'agent', '') = ''
       OR COALESCE(usage->>'model', '') = ''
       OR COALESCE(usage->>'project_hash', '') = ''
       OR COALESCE(usage->>'fingerprint', '') = '' THEN
        RAISE EXCEPTION 'invalid Orca thread usage payload';
    END IF;

    INSERT INTO observability.orca_thread_usage_daily AS current (
        event_id, thread_id, thread_name, skill_names, usage_date, agent, model, project_label, project_hash,
        request_count, input_tokens, cache_read_tokens, cache_write_tokens,
        output_tokens, reasoning_tokens, total_tokens, fingerprint,
        collector_id, first_observed_at, last_observed_at
    ) VALUES (
        usage->>'event_id', usage->>'thread_id', NULLIF(usage->>'thread_name', ''),
        COALESCE(usage->'skill_names', '[]'::jsonb),
        (usage->>'usage_date')::date,
        usage->>'agent', usage->>'model',
        COALESCE(usage->>'project_label', 'unknown'), usage->>'project_hash',
        COALESCE((usage->>'request_count')::bigint, 0),
        COALESCE((usage->>'input_tokens')::bigint, 0),
        COALESCE((usage->>'cache_read_tokens')::bigint, 0),
        COALESCE((usage->>'cache_write_tokens')::bigint, 0),
        COALESCE((usage->>'output_tokens')::bigint, 0),
        COALESCE((usage->>'reasoning_tokens')::bigint, 0),
        COALESCE((usage->>'total_tokens')::bigint, 0), usage->>'fingerprint',
        incoming_collector_id, observed_at, observed_at
    )
    ON CONFLICT (event_id) DO UPDATE SET
        thread_id = EXCLUDED.thread_id,
        thread_name = COALESCE(EXCLUDED.thread_name, current.thread_name),
        skill_names = EXCLUDED.skill_names,
        usage_date = EXCLUDED.usage_date,
        agent = EXCLUDED.agent,
        model = EXCLUDED.model,
        project_label = EXCLUDED.project_label,
        project_hash = EXCLUDED.project_hash,
        request_count = EXCLUDED.request_count,
        input_tokens = EXCLUDED.input_tokens,
        cache_read_tokens = EXCLUDED.cache_read_tokens,
        cache_write_tokens = EXCLUDED.cache_write_tokens,
        output_tokens = EXCLUDED.output_tokens,
        reasoning_tokens = EXCLUDED.reasoning_tokens,
        total_tokens = EXCLUDED.total_tokens,
        fingerprint = EXCLUDED.fingerprint,
        collector_id = EXCLUDED.collector_id,
        last_observed_at = EXCLUDED.last_observed_at
    WHERE current.fingerprint IS DISTINCT FROM EXCLUDED.fingerprint;

    GET DIAGNOSTICS changed_rows = ROW_COUNT;
    RETURN changed_rows > 0;
END;
$$;

REVOKE ALL ON FUNCTION observability.ingest_orca_thread_usage(jsonb, text, timestamptz) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION observability.ingest_orca_thread_usage(jsonb, text, timestamptz) TO :"app_user";

CREATE OR REPLACE FUNCTION observability.ingest_hermes_goal_run(
    goal_run jsonb,
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
    IF COALESCE(goal_run->>'event_id', '') = ''
       OR COALESCE(goal_run->>'session_id', '') = ''
       OR COALESCE(goal_run->>'profile', '') = ''
       OR COALESCE(goal_run->>'model', '') = ''
       OR COALESCE(goal_run->>'status', '') = ''
       OR COALESCE(goal_run->>'started_at', '') = ''
       OR COALESCE(goal_run->>'fingerprint', '') = '' THEN
        RAISE EXCEPTION 'invalid Hermes goal run payload';
    END IF;

    INSERT INTO observability.hermes_goal_runs AS current (
        event_id, session_id, profile, model, status, turns_used, max_turns,
        started_at, ended_at, duration_ms, last_verdict, paused_reason,
        fingerprint, collector_id, first_observed_at, last_observed_at
    ) VALUES (
        goal_run->>'event_id', goal_run->>'session_id', goal_run->>'profile',
        goal_run->>'model', goal_run->>'status',
        COALESCE((goal_run->>'turns_used')::integer, 0),
        COALESCE((goal_run->>'max_turns')::integer, 0),
        (goal_run->>'started_at')::timestamptz,
        NULLIF(goal_run->>'ended_at', '')::timestamptz,
        NULLIF(goal_run->>'duration_ms', '')::bigint,
        NULLIF(goal_run->>'last_verdict', ''),
        NULLIF(goal_run->>'paused_reason', ''),
        goal_run->>'fingerprint', incoming_collector_id, observed_at, observed_at
    )
    ON CONFLICT (event_id) DO UPDATE SET
        session_id = EXCLUDED.session_id,
        model = EXCLUDED.model,
        status = EXCLUDED.status,
        turns_used = EXCLUDED.turns_used,
        max_turns = EXCLUDED.max_turns,
        ended_at = EXCLUDED.ended_at,
        duration_ms = EXCLUDED.duration_ms,
        last_verdict = EXCLUDED.last_verdict,
        paused_reason = EXCLUDED.paused_reason,
        fingerprint = EXCLUDED.fingerprint,
        collector_id = EXCLUDED.collector_id,
        last_observed_at = EXCLUDED.last_observed_at
    WHERE current.fingerprint IS DISTINCT FROM EXCLUDED.fingerprint;

    GET DIAGNOSTICS changed_rows = ROW_COUNT;
    RETURN changed_rows > 0;
END;
$$;

CREATE OR REPLACE FUNCTION observability.ingest_hermes_kanban_task(
    task jsonb,
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
    IF COALESCE(task->>'event_id', '') = ''
       OR COALESCE(task->>'board', '') = ''
       OR COALESCE(task->>'task_id', '') = ''
       OR COALESCE(task->>'status', '') = ''
       OR COALESCE(task->>'fingerprint', '') = '' THEN
        RAISE EXCEPTION 'invalid Hermes kanban task payload';
    END IF;

    INSERT INTO observability.hermes_kanban_tasks AS current (
        event_id, board, task_id, title, title_hash, assignee, status, priority,
        created_by, created_at, started_at, completed_at, workspace_kind,
        workspace_present, project_id, tenant, result_present, idempotency_key,
        consecutive_failures, worker_pid_present, last_failure_present,
        max_runtime_seconds, last_heartbeat_at, current_run_id,
        workflow_template_id, current_step_key, skill_count, model_override,
        max_retries, goal_mode, goal_max_turns, session_id, block_kind,
        block_recurrences, parent_count, child_count, comment_count,
        attachment_count, run_count, latest_run_id, latest_run_status,
        latest_run_outcome, latest_run_started_at, latest_run_ended_at,
        fingerprint, collector_id, first_observed_at, last_observed_at
    ) VALUES (
        task->>'event_id', task->>'board', task->>'task_id',
        NULLIF(task->>'title', ''), NULLIF(task->>'title_hash', ''),
        NULLIF(task->>'assignee', ''), COALESCE(task->>'status', 'unknown'),
        COALESCE((task->>'priority')::integer, 0), NULLIF(task->>'created_by', ''),
        NULLIF(task->>'created_at', '')::timestamptz,
        NULLIF(task->>'started_at', '')::timestamptz,
        NULLIF(task->>'completed_at', '')::timestamptz,
        NULLIF(task->>'workspace_kind', ''),
        COALESCE((task->>'workspace_present')::boolean, false),
        NULLIF(task->>'project_id', ''), NULLIF(task->>'tenant', ''),
        COALESCE((task->>'result_present')::boolean, false),
        NULLIF(task->>'idempotency_key', ''),
        COALESCE((task->>'consecutive_failures')::integer, 0),
        COALESCE((task->>'worker_pid_present')::boolean, false),
        COALESCE((task->>'last_failure_present')::boolean, false),
        NULLIF(task->>'max_runtime_seconds', '')::integer,
        NULLIF(task->>'last_heartbeat_at', '')::timestamptz,
        NULLIF(task->>'current_run_id', '')::bigint,
        NULLIF(task->>'workflow_template_id', ''), NULLIF(task->>'current_step_key', ''),
        COALESCE((task->>'skill_count')::integer, 0),
        NULLIF(task->>'model_override', ''), NULLIF(task->>'max_retries', '')::integer,
        COALESCE((task->>'goal_mode')::boolean, false),
        NULLIF(task->>'goal_max_turns', '')::integer,
        NULLIF(task->>'session_id', ''), NULLIF(task->>'block_kind', ''),
        COALESCE((task->>'block_recurrences')::integer, 0),
        COALESCE((task->>'parent_count')::integer, 0),
        COALESCE((task->>'child_count')::integer, 0),
        COALESCE((task->>'comment_count')::integer, 0),
        COALESCE((task->>'attachment_count')::integer, 0),
        COALESCE((task->>'run_count')::integer, 0),
        NULLIF(task->>'latest_run_id', '')::bigint,
        NULLIF(task->>'latest_run_status', ''),
        NULLIF(task->>'latest_run_outcome', ''),
        NULLIF(task->>'latest_run_started_at', '')::timestamptz,
        NULLIF(task->>'latest_run_ended_at', '')::timestamptz,
        task->>'fingerprint', incoming_collector_id, observed_at, observed_at
    )
    ON CONFLICT (event_id) DO UPDATE SET
        title = EXCLUDED.title,
        title_hash = EXCLUDED.title_hash,
        assignee = EXCLUDED.assignee,
        status = EXCLUDED.status,
        priority = EXCLUDED.priority,
        created_by = EXCLUDED.created_by,
        created_at = EXCLUDED.created_at,
        started_at = EXCLUDED.started_at,
        completed_at = EXCLUDED.completed_at,
        workspace_kind = EXCLUDED.workspace_kind,
        workspace_present = EXCLUDED.workspace_present,
        project_id = EXCLUDED.project_id,
        tenant = EXCLUDED.tenant,
        result_present = EXCLUDED.result_present,
        idempotency_key = EXCLUDED.idempotency_key,
        consecutive_failures = EXCLUDED.consecutive_failures,
        worker_pid_present = EXCLUDED.worker_pid_present,
        last_failure_present = EXCLUDED.last_failure_present,
        max_runtime_seconds = EXCLUDED.max_runtime_seconds,
        last_heartbeat_at = EXCLUDED.last_heartbeat_at,
        current_run_id = EXCLUDED.current_run_id,
        workflow_template_id = EXCLUDED.workflow_template_id,
        current_step_key = EXCLUDED.current_step_key,
        skill_count = EXCLUDED.skill_count,
        model_override = EXCLUDED.model_override,
        max_retries = EXCLUDED.max_retries,
        goal_mode = EXCLUDED.goal_mode,
        goal_max_turns = EXCLUDED.goal_max_turns,
        session_id = EXCLUDED.session_id,
        block_kind = EXCLUDED.block_kind,
        block_recurrences = EXCLUDED.block_recurrences,
        parent_count = EXCLUDED.parent_count,
        child_count = EXCLUDED.child_count,
        comment_count = EXCLUDED.comment_count,
        attachment_count = EXCLUDED.attachment_count,
        run_count = EXCLUDED.run_count,
        latest_run_id = EXCLUDED.latest_run_id,
        latest_run_status = EXCLUDED.latest_run_status,
        latest_run_outcome = EXCLUDED.latest_run_outcome,
        latest_run_started_at = EXCLUDED.latest_run_started_at,
        latest_run_ended_at = EXCLUDED.latest_run_ended_at,
        fingerprint = EXCLUDED.fingerprint,
        collector_id = EXCLUDED.collector_id,
        last_observed_at = EXCLUDED.last_observed_at
    WHERE current.fingerprint IS DISTINCT FROM EXCLUDED.fingerprint;

    GET DIAGNOSTICS changed_rows = ROW_COUNT;
    RETURN changed_rows > 0;
END;
$$;

CREATE OR REPLACE FUNCTION observability.ingest_hermes_kanban_task_run(
    run jsonb,
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
    IF COALESCE(run->>'event_id', '') = ''
       OR COALESCE(run->>'board', '') = ''
       OR COALESCE(run->>'run_id', '') = ''
       OR COALESCE(run->>'task_id', '') = ''
       OR COALESCE(run->>'status', '') = ''
       OR COALESCE(run->>'fingerprint', '') = '' THEN
        RAISE EXCEPTION 'invalid Hermes kanban task run payload';
    END IF;

    INSERT INTO observability.hermes_kanban_task_runs AS current (
        event_id, board, run_id, task_id, profile, step_key, status,
        claim_lock_present, claim_expires_at, worker_pid_present,
        max_runtime_seconds, last_heartbeat_at, started_at, ended_at, outcome,
        summary_present, metadata_keys, error_present, fingerprint,
        collector_id, first_observed_at, last_observed_at
    ) VALUES (
        run->>'event_id', run->>'board', (run->>'run_id')::bigint,
        run->>'task_id', NULLIF(run->>'profile', ''), NULLIF(run->>'step_key', ''),
        COALESCE(run->>'status', 'unknown'),
        COALESCE((run->>'claim_lock_present')::boolean, false),
        NULLIF(run->>'claim_expires_at', '')::timestamptz,
        COALESCE((run->>'worker_pid_present')::boolean, false),
        NULLIF(run->>'max_runtime_seconds', '')::integer,
        NULLIF(run->>'last_heartbeat_at', '')::timestamptz,
        NULLIF(run->>'started_at', '')::timestamptz,
        NULLIF(run->>'ended_at', '')::timestamptz,
        NULLIF(run->>'outcome', ''),
        COALESCE((run->>'summary_present')::boolean, false),
        COALESCE(run->'metadata_keys', '[]'::jsonb),
        COALESCE((run->>'error_present')::boolean, false),
        run->>'fingerprint', incoming_collector_id, observed_at, observed_at
    )
    ON CONFLICT (event_id) DO UPDATE SET
        profile = EXCLUDED.profile,
        step_key = EXCLUDED.step_key,
        status = EXCLUDED.status,
        claim_lock_present = EXCLUDED.claim_lock_present,
        claim_expires_at = EXCLUDED.claim_expires_at,
        worker_pid_present = EXCLUDED.worker_pid_present,
        max_runtime_seconds = EXCLUDED.max_runtime_seconds,
        last_heartbeat_at = EXCLUDED.last_heartbeat_at,
        started_at = EXCLUDED.started_at,
        ended_at = EXCLUDED.ended_at,
        outcome = EXCLUDED.outcome,
        summary_present = EXCLUDED.summary_present,
        metadata_keys = EXCLUDED.metadata_keys,
        error_present = EXCLUDED.error_present,
        fingerprint = EXCLUDED.fingerprint,
        collector_id = EXCLUDED.collector_id,
        last_observed_at = EXCLUDED.last_observed_at
    WHERE current.fingerprint IS DISTINCT FROM EXCLUDED.fingerprint;

    GET DIAGNOSTICS changed_rows = ROW_COUNT;
    RETURN changed_rows > 0;
END;
$$;

CREATE OR REPLACE FUNCTION observability.ingest_hermes_kanban_task_event(
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
    IF COALESCE(event->>'event_id', '') = ''
       OR COALESCE(event->>'board', '') = ''
       OR COALESCE(event->>'source_event_id', '') = ''
       OR COALESCE(event->>'task_id', '') = ''
       OR COALESCE(event->>'kind', '') = ''
       OR COALESCE(event->>'fingerprint', '') = '' THEN
        RAISE EXCEPTION 'invalid Hermes kanban task event payload';
    END IF;

    INSERT INTO observability.hermes_kanban_task_events AS current (
        event_id, board, source_event_id, task_id, run_id, kind,
        payload_present, payload_keys, created_at, fingerprint, collector_id,
        first_observed_at, last_observed_at
    ) VALUES (
        event->>'event_id', event->>'board', (event->>'source_event_id')::bigint,
        event->>'task_id', NULLIF(event->>'run_id', '')::bigint,
        event->>'kind', COALESCE((event->>'payload_present')::boolean, false),
        COALESCE(event->'payload_keys', '[]'::jsonb),
        NULLIF(event->>'created_at', '')::timestamptz,
        event->>'fingerprint', incoming_collector_id, observed_at, observed_at
    )
    ON CONFLICT (event_id) DO UPDATE SET
        run_id = EXCLUDED.run_id,
        kind = EXCLUDED.kind,
        payload_present = EXCLUDED.payload_present,
        payload_keys = EXCLUDED.payload_keys,
        created_at = EXCLUDED.created_at,
        fingerprint = EXCLUDED.fingerprint,
        collector_id = EXCLUDED.collector_id,
        last_observed_at = EXCLUDED.last_observed_at
    WHERE current.fingerprint IS DISTINCT FROM EXCLUDED.fingerprint;

    GET DIAGNOSTICS changed_rows = ROW_COUNT;
    RETURN changed_rows > 0;
END;
$$;

CREATE OR REPLACE FUNCTION observability.ingest_hermes_kanban_task_link(
    link jsonb,
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
    IF COALESCE(link->>'event_id', '') = ''
       OR COALESCE(link->>'board', '') = ''
       OR COALESCE(link->>'parent_id', '') = ''
       OR COALESCE(link->>'child_id', '') = ''
       OR COALESCE(link->>'fingerprint', '') = '' THEN
        RAISE EXCEPTION 'invalid Hermes kanban task link payload';
    END IF;

    INSERT INTO observability.hermes_kanban_task_links AS current (
        event_id, board, parent_id, child_id, fingerprint, collector_id,
        first_observed_at, last_observed_at
    ) VALUES (
        link->>'event_id', link->>'board', link->>'parent_id', link->>'child_id',
        link->>'fingerprint', incoming_collector_id, observed_at, observed_at
    )
    ON CONFLICT (event_id) DO UPDATE SET
        fingerprint = EXCLUDED.fingerprint,
        collector_id = EXCLUDED.collector_id,
        last_observed_at = EXCLUDED.last_observed_at
    WHERE current.fingerprint IS DISTINCT FROM EXCLUDED.fingerprint;

    GET DIAGNOSTICS changed_rows = ROW_COUNT;
    RETURN changed_rows > 0;
END;
$$;

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

REVOKE ALL ON FUNCTION observability.ingest_hermes_goal_run(jsonb, text, timestamptz) FROM PUBLIC;
REVOKE ALL ON FUNCTION observability.ingest_hermes_kanban_task(jsonb, text, timestamptz) FROM PUBLIC;
REVOKE ALL ON FUNCTION observability.ingest_hermes_kanban_task_run(jsonb, text, timestamptz) FROM PUBLIC;
REVOKE ALL ON FUNCTION observability.ingest_hermes_kanban_task_event(jsonb, text, timestamptz) FROM PUBLIC;
REVOKE ALL ON FUNCTION observability.ingest_hermes_kanban_task_link(jsonb, text, timestamptz) FROM PUBLIC;
REVOKE ALL ON FUNCTION observability.ingest_hermes_telemetry(text, jsonb, text, timestamptz) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION observability.ingest_hermes_telemetry(text, jsonb, text, timestamptz) TO :"app_user";

GRANT SELECT ON ALL TABLES IN SCHEMA observability TO :"app_user";
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA observability TO :"app_user";
ALTER DEFAULT PRIVILEGES IN SCHEMA observability
    GRANT SELECT ON TABLES TO :"app_user";
ALTER DEFAULT PRIVILEGES IN SCHEMA observability
    GRANT USAGE, SELECT ON SEQUENCES TO :"app_user";

GRANT USAGE ON SCHEMA observability TO :"backup_user";
GRANT SELECT ON ALL TABLES IN SCHEMA observability TO :"backup_user";
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA observability TO :"backup_user";
ALTER DEFAULT PRIVILEGES IN SCHEMA observability
    GRANT SELECT ON TABLES TO :"backup_user";
ALTER DEFAULT PRIVILEGES IN SCHEMA observability
    GRANT USAGE, SELECT ON SEQUENCES TO :"backup_user";

DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'grafana_reader') THEN
        GRANT SELECT ON observability.orca_thread_usage_daily TO grafana_reader;
    END IF;
END;
$$;
SQL
