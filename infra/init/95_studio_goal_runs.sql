-- Studio Assistant v2 — durable cartridge goal runs.
--
-- These tables are intentionally Studio-scoped. They do not reuse the global
-- copilot workflow tables because Studio needs a tighter approval model around
-- cartridge authoring actions and should remain deployable without changing the
-- global Copilot surface.

CREATE EXTENSION IF NOT EXISTS pgcrypto;

CREATE TABLE IF NOT EXISTS studio_goal_runs (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id       BIGINT REFERENCES users(id) ON DELETE SET NULL,
    cartridge_id  TEXT NOT NULL,
    intent        TEXT NOT NULL,
    plan          JSONB NOT NULL DEFAULT '[]'::jsonb,
    status        TEXT NOT NULL DEFAULT 'planning'
                  CHECK (status IN ('planning', 'running', 'waiting_approval', 'completed', 'failed', 'cancelled')),
    current_step  INTEGER NOT NULL DEFAULT 0,
    result        JSONB NOT NULL DEFAULT '{}'::jsonb,
    error         TEXT,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    finished_at   TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_studio_goal_runs_user_created
    ON studio_goal_runs (user_id, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_studio_goal_runs_cartridge_status
    ON studio_goal_runs (cartridge_id, status);

CREATE TABLE IF NOT EXISTS studio_goal_steps (
    id             BIGSERIAL PRIMARY KEY,
    goal_run_id    UUID NOT NULL REFERENCES studio_goal_runs(id) ON DELETE CASCADE,
    step_idx       INTEGER NOT NULL,
    step_key       TEXT NOT NULL,
    title          TEXT NOT NULL,
    description    TEXT NOT NULL DEFAULT '',
    tool           TEXT,
    args           JSONB NOT NULL DEFAULT '{}'::jsonb,
    risk_level     TEXT NOT NULL DEFAULT 'read'
                   CHECK (risk_level IN ('read', 'write', 'destructive')),
    status         TEXT NOT NULL DEFAULT 'pending'
                   CHECK (status IN ('pending', 'running', 'waiting_approval', 'completed', 'failed', 'skipped')),
    result         JSONB,
    approval_key   TEXT,
    started_at     TIMESTAMPTZ,
    finished_at    TIMESTAMPTZ,
    UNIQUE (goal_run_id, step_idx),
    UNIQUE (goal_run_id, step_key)
);

CREATE INDEX IF NOT EXISTS idx_studio_goal_steps_run_idx
    ON studio_goal_steps (goal_run_id, step_idx);

CREATE INDEX IF NOT EXISTS idx_studio_goal_steps_status
    ON studio_goal_steps (status)
    WHERE status IN ('pending', 'running', 'waiting_approval');

GRANT SELECT, INSERT, UPDATE ON studio_goal_runs TO omega_console;
GRANT SELECT, INSERT, UPDATE ON studio_goal_steps TO omega_console;
GRANT USAGE, SELECT ON SEQUENCE studio_goal_steps_id_seq TO omega_console;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'studio_goal_runs_status_check'
    ) THEN
        ALTER TABLE studio_goal_runs
            ADD CONSTRAINT studio_goal_runs_status_check
            CHECK (status IN ('planning', 'running', 'waiting_approval', 'completed', 'failed', 'cancelled'));
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'studio_goal_steps_status_check'
    ) THEN
        ALTER TABLE studio_goal_steps
            ADD CONSTRAINT studio_goal_steps_status_check
            CHECK (status IN ('pending', 'running', 'waiting_approval', 'completed', 'failed', 'skipped'));
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'studio_goal_steps_risk_level_check'
    ) THEN
        ALTER TABLE studio_goal_steps
            ADD CONSTRAINT studio_goal_steps_risk_level_check
            CHECK (risk_level IN ('read', 'write', 'destructive'));
    END IF;
END $$;

INSERT INTO schema_migrations (filename, applied_at)
VALUES ('95_studio_goal_runs.sql', NOW())
ON CONFLICT (filename) DO NOTHING;
