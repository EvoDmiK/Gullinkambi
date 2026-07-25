-- Hermes Kanban Grafana panel queries.
-- Data source: PostgreSQL/TimescaleDB with the observability schema.

-- 1. Current task status by board.
SELECT
  board,
  status,
  count(*) AS tasks
FROM observability.hermes_kanban_tasks
WHERE $__timeFilter(created_at)
GROUP BY board, status
ORDER BY board, status;

-- 2. Open, blocked, or triage tasks that need attention.
SELECT
  COALESCE(last_heartbeat_at, started_at, created_at) AS time,
  board,
  task_id,
  title,
  title_hash,
  assignee,
  status,
  latest_run_status,
  latest_run_outcome,
  consecutive_failures,
  block_kind,
  run_count,
  result_present
FROM observability.hermes_kanban_tasks
WHERE status IN ('running', 'blocked', 'triage')
ORDER BY time DESC
LIMIT 100;

-- 3. Task creation throughput.
SELECT
  time_bucket('$__interval', created_at) AS time,
  board,
  count(*) AS tasks_created
FROM observability.hermes_kanban_tasks
WHERE $__timeFilter(created_at)
GROUP BY 1, board
ORDER BY 1, board;

-- 4. Completed task throughput.
SELECT
  time_bucket('$__interval', completed_at) AS time,
  board,
  assignee,
  count(*) AS tasks_completed
FROM observability.hermes_kanban_tasks
WHERE $__timeFilter(completed_at)
  AND completed_at IS NOT NULL
GROUP BY 1, board, assignee
ORDER BY 1, board, assignee;

-- 5. Worker run result trend.
SELECT
  time_bucket('$__interval', COALESCE(ended_at, started_at)) AS time,
  board,
  status,
  count(*) AS runs
FROM observability.hermes_kanban_task_runs
WHERE $__timeFilter(COALESCE(ended_at, started_at))
GROUP BY 1, board, status
ORDER BY 1, board, status;

-- 6. Stale running tasks.
SELECT
  COALESCE(last_heartbeat_at, started_at, created_at) AS time,
  board,
  task_id,
  title,
  title_hash,
  assignee,
  status,
  latest_run_status,
  last_heartbeat_at,
  max_runtime_seconds,
  consecutive_failures
FROM observability.hermes_kanban_tasks
WHERE status = 'running'
  AND (
    last_heartbeat_at IS NULL
    OR last_heartbeat_at < now() - interval '10 minutes'
  )
ORDER BY time ASC
LIMIT 100;

-- 7. Event volume by kind.
SELECT
  time_bucket('$__interval', created_at) AS time,
  board,
  kind,
  count(*) AS events
FROM observability.hermes_kanban_task_events
WHERE $__timeFilter(created_at)
GROUP BY 1, board, kind
ORDER BY 1, board, kind;

-- 8. Dependency fan-in/fan-out.
SELECT
  board,
  task_id,
  title,
  title_hash,
  assignee,
  status,
  parent_count,
  child_count,
  run_count,
  comment_count,
  attachment_count
FROM observability.hermes_kanban_tasks
WHERE parent_count > 0 OR child_count > 0
ORDER BY child_count DESC, parent_count DESC, created_at DESC
LIMIT 100;
