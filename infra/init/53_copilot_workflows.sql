-- Sprint v1.44.2 (Tarea I — capability 16) — copilot workflows.
--
-- "Operar procesos completos" — the user says "close the month for
-- replicon" and the copilot plans + executes a multi-step workflow
-- with intermediate progress reports. Each workflow has steps; each
-- step has a tool invocation and a result.
--
-- The actual LLM-driven planning + step execution loop lives in
-- copilot_service (next-session integration); this migration just
-- provides the durable surface so the streaming endpoint can poll
-- workflow_steps as they progress.

CREATE TABLE IF NOT EXISTS workflow_runs (
    id              UUID         PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id         BIGINT       NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    conversation_id UUID,
        -- nullable: workflows can be launched outside a conversation
        -- (e.g. from /dashboard "Recurring tasks"). FK added below
        -- with the same conversations-exists guard pattern as
        -- migration 51.
    intent          TEXT         NOT NULL,
        -- user-facing description; what they ASKED for.
    plan            JSONB        NOT NULL DEFAULT '[]'::jsonb,
        -- LLM-generated step list, an array of
        -- {step_id, description, tool, args} dicts.
    status          TEXT         NOT NULL DEFAULT 'planning',
        -- planning | running | completed | failed | cancelled
    current_step    INTEGER      NOT NULL DEFAULT 0,
    error           TEXT,
    created_at      TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    finished_at     TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_workflow_runs_user_created
    ON workflow_runs (user_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_workflow_runs_status
    ON workflow_runs (status)
 WHERE status IN ('planning', 'running');

CREATE TABLE IF NOT EXISTS workflow_steps (
    id              BIGSERIAL    PRIMARY KEY,
    workflow_id     UUID         NOT NULL REFERENCES workflow_runs(id) ON DELETE CASCADE,
    step_idx        INTEGER      NOT NULL,
    description     TEXT         NOT NULL,
    tool            TEXT,
    args            JSONB        NOT NULL DEFAULT '{}'::jsonb,
    result          JSONB,
    status          TEXT         NOT NULL DEFAULT 'pending',
        -- pending | running | completed | failed | skipped
    started_at      TIMESTAMPTZ,
    finished_at     TIMESTAMPTZ,
    UNIQUE (workflow_id, step_idx)
);

CREATE INDEX IF NOT EXISTS idx_workflow_steps_workflow
    ON workflow_steps (workflow_id, step_idx);

-- Attach the conversations FK if both sides exist.
DO $$
BEGIN
  IF EXISTS (
    SELECT 1 FROM information_schema.tables
     WHERE table_schema = 'public' AND table_name = 'conversations'
  ) AND NOT EXISTS (
    SELECT 1 FROM information_schema.table_constraints
     WHERE table_schema = 'public'
       AND table_name = 'workflow_runs'
       AND constraint_type = 'FOREIGN KEY'
       AND constraint_name = 'workflow_runs_conversation_id_fkey'
  ) THEN
    ALTER TABLE workflow_runs
        ADD CONSTRAINT workflow_runs_conversation_id_fkey
        FOREIGN KEY (conversation_id) REFERENCES conversations(id) ON DELETE SET NULL;
  END IF;
END $$;

-- Self-register.
INSERT INTO schema_migrations (filename, applied_at)
VALUES ('53_copilot_workflows.sql', NOW())
ON CONFLICT (filename) DO NOTHING;
