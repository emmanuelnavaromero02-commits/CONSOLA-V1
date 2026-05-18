-- v1.44.6 Task 1 — workflow executor runtime state.
--
-- Migration 53 created workflow_runs/workflow_steps and already includes:
-- status, current_step, error, created_at, finished_at, and per-step result.
-- The executor adds explicit runtime timestamps plus a denormalised
-- step_results snapshot for fast status polling.

ALTER TABLE workflow_runs
    ADD COLUMN IF NOT EXISTS started_at TIMESTAMPTZ;

ALTER TABLE workflow_runs
    ADD COLUMN IF NOT EXISTS completed_at TIMESTAMPTZ;

ALTER TABLE workflow_runs
    ADD COLUMN IF NOT EXISTS step_results JSONB NOT NULL DEFAULT '[]'::jsonb;

CREATE INDEX IF NOT EXISTS idx_workflow_runs_started
    ON workflow_runs (started_at DESC)
    WHERE started_at IS NOT NULL;

INSERT INTO schema_migrations (filename, applied_at)
VALUES ('56_workflow_executor_runtime.sql', NOW())
ON CONFLICT (filename) DO NOTHING;
