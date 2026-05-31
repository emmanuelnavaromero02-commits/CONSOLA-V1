-- Sprint v1.45 — Copilot Cúspide
--
-- Three durable surfaces that lift the copilot from "responds with
-- tool calls" to "solves business goals end-to-end, orchestrates
-- cartridge watchdogs, and learns from approved/declined decisions".
--
--   * copilot_goals     — high-level objectives ("fix the margin")
--                          that decompose into one or more
--                          workflow_runs. The goal owns the user
--                          intent, the LLM's diagnosis, and the
--                          aggregated outcome.
--   * copilot_lessons   — durable rules harvested from approved/
--                          declined actions. Injected into the system
--                          prompt on the next turn so the copilot
--                          stops asking the same question twice.
--   * copilot_watchdogs — registry of cartridge-side specialised
--                          agents (forecast_watchdog, margin_watchdog,
--                          ...). The copilot picks the relevant
--                          watchdog by keyword and asks it for a
--                          focused diagnosis instead of brute-forcing
--                          with raw tools.
--
-- All three are workspace-scoped where the FK exists; otherwise
-- user_id is the tenant boundary.

CREATE TABLE IF NOT EXISTS copilot_goals (
    id              UUID         PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id         BIGINT       NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    workspace_id    UUID,
    conversation_id UUID,
    goal_text       TEXT         NOT NULL,
        -- the user's objective in their own words
    plan_summary    TEXT,
        -- short LLM-generated description of the strategy
    status          TEXT         NOT NULL DEFAULT 'planning',
        -- planning | running | awaiting_approval | completed | failed | cancelled
    impact_estimate JSONB        NOT NULL DEFAULT '{}'::jsonb,
        -- {currency: "MXN", amount: 89000, direction: "save"|"recover"|"avoid"}
    outcome_summary TEXT,
        -- final LLM summary once status reaches a terminal state
    workflow_ids    UUID[]       NOT NULL DEFAULT ARRAY[]::UUID[],
    metadata        JSONB        NOT NULL DEFAULT '{}'::jsonb,
    created_at      TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    finished_at     TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_copilot_goals_user_created
    ON copilot_goals (user_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_copilot_goals_user_status
    ON copilot_goals (user_id, status);
CREATE INDEX IF NOT EXISTS idx_copilot_goals_active
    ON copilot_goals (status)
 WHERE status IN ('planning', 'running', 'awaiting_approval');


CREATE TABLE IF NOT EXISTS copilot_lessons (
    id              UUID         PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id         BIGINT       REFERENCES users(id) ON DELETE CASCADE,
        -- nullable: workspace-wide lessons (admin promoted) leave
        -- user_id null and rely on workspace_id below.
    workspace_id    UUID,
    scope           TEXT         NOT NULL DEFAULT 'user',
        -- user | workspace | global
    trigger_pattern TEXT         NOT NULL,
        -- short natural-language hint matched against intent keywords
    lesson_text     TEXT         NOT NULL,
        -- the rule to inject ("project with >300h non-billable → alert")
    source_kind     TEXT         NOT NULL DEFAULT 'approval',
        -- approval | decline | manual | system
    source_ref      TEXT,
        -- optional: workflow_id, conversation_id, etc.
    confidence      REAL         NOT NULL DEFAULT 1.0
        CHECK (confidence >= 0.0 AND confidence <= 1.0),
    applies_to      JSONB        NOT NULL DEFAULT '{}'::jsonb,
        -- {cartridges: ["replicon"], tools: ["query_kb"]}
    enabled         BOOLEAN      NOT NULL DEFAULT TRUE,
    hits            INTEGER      NOT NULL DEFAULT 0,
        -- bumped each time the lesson is injected, for visibility
    last_used_at    TIMESTAMPTZ,
    created_at      TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_copilot_lessons_user_enabled
    ON copilot_lessons (user_id, enabled, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_copilot_lessons_workspace
    ON copilot_lessons (workspace_id, enabled)
 WHERE workspace_id IS NOT NULL;


CREATE TABLE IF NOT EXISTS copilot_watchdogs (
    id              UUID         PRIMARY KEY DEFAULT gen_random_uuid(),
    cartridge_id    TEXT         NOT NULL,
    slug            TEXT         NOT NULL,
    name            TEXT         NOT NULL,
    description     TEXT         NOT NULL DEFAULT '',
    intent_keywords TEXT[]       NOT NULL DEFAULT ARRAY[]::TEXT[],
        -- ["margin", "rentabilidad", "billable"] — matched against
        -- the user's goal/intent for orchestration routing.
    agent_slug      TEXT,
        -- optional: pointer into the `agents` table for an existing
        -- runtime-defined agent. NULL means the watchdog is purely
        -- a tool catalog reference.
    tools           TEXT[]       NOT NULL DEFAULT ARRAY[]::TEXT[],
        -- e.g. ["replicon.query_kb:project_financial_summary"]
    risk_level      TEXT         NOT NULL DEFAULT 'read'
        CHECK (risk_level IN ('read','write','destructive')),
    enabled         BOOLEAN      NOT NULL DEFAULT TRUE,
    metadata        JSONB        NOT NULL DEFAULT '{}'::jsonb,
    created_at      TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    UNIQUE (cartridge_id, slug)
);

CREATE INDEX IF NOT EXISTS idx_copilot_watchdogs_enabled
    ON copilot_watchdogs (cartridge_id, enabled);


-- ── CHECK constraints (added in audit round 1) ──────────────────────
--
-- We validate the same enums in Python (lessons_service.record_lesson,
-- goal_solver.update_goal_status). Belt-and-braces: a bad UPDATE issued
-- straight at psql shouldn't be able to corrupt the column. Both
-- constraints are added idempotently via DO blocks so re-running the
-- migration on an already-provisioned database is a no-op.

DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM information_schema.table_constraints
     WHERE table_schema = 'public'
       AND table_name = 'copilot_goals'
       AND constraint_name = 'copilot_goals_status_check'
  ) THEN
    ALTER TABLE copilot_goals
      ADD CONSTRAINT copilot_goals_status_check
      CHECK (status IN (
          'planning', 'running', 'awaiting_approval',
          'completed', 'failed', 'cancelled'
      ));
  END IF;
END $$;

DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM information_schema.table_constraints
     WHERE table_schema = 'public'
       AND table_name = 'copilot_lessons'
       AND constraint_name = 'copilot_lessons_scope_check'
  ) THEN
    ALTER TABLE copilot_lessons
      ADD CONSTRAINT copilot_lessons_scope_check
      CHECK (scope IN ('user', 'workspace', 'global'));
  END IF;
END $$;

DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM information_schema.table_constraints
     WHERE table_schema = 'public'
       AND table_name = 'copilot_lessons'
       AND constraint_name = 'copilot_lessons_source_kind_check'
  ) THEN
    ALTER TABLE copilot_lessons
      ADD CONSTRAINT copilot_lessons_source_kind_check
      CHECK (source_kind IN ('approval', 'decline', 'manual', 'system'));
  END IF;
END $$;


-- ── FK references (guarded; only attach if the referenced table is
-- already present). conversations + workspaces both exist in deploys
-- that ran migrations 13 / 38 first, but we still guard the ADD
-- CONSTRAINT so this file is safe to re-run on a partial schema.

DO $$
BEGIN
  IF EXISTS (
    SELECT 1 FROM information_schema.tables
     WHERE table_schema = 'public' AND table_name = 'conversations'
  ) AND NOT EXISTS (
    SELECT 1 FROM information_schema.table_constraints
     WHERE table_schema = 'public'
       AND table_name = 'copilot_goals'
       AND constraint_name = 'copilot_goals_conversation_id_fkey'
  ) THEN
    ALTER TABLE copilot_goals
      ADD CONSTRAINT copilot_goals_conversation_id_fkey
      FOREIGN KEY (conversation_id)
      REFERENCES conversations(id)
      ON DELETE SET NULL;
  END IF;
END $$;

DO $$
BEGIN
  IF EXISTS (
    SELECT 1 FROM information_schema.tables
     WHERE table_schema = 'public' AND table_name = 'workspaces'
  ) AND NOT EXISTS (
    SELECT 1 FROM information_schema.table_constraints
     WHERE table_schema = 'public'
       AND table_name = 'copilot_goals'
       AND constraint_name = 'copilot_goals_workspace_id_fkey'
  ) THEN
    ALTER TABLE copilot_goals
      ADD CONSTRAINT copilot_goals_workspace_id_fkey
      FOREIGN KEY (workspace_id)
      REFERENCES workspaces(id)
      ON DELETE SET NULL;
  END IF;
END $$;

DO $$
BEGIN
  IF EXISTS (
    SELECT 1 FROM information_schema.tables
     WHERE table_schema = 'public' AND table_name = 'workspaces'
  ) AND NOT EXISTS (
    SELECT 1 FROM information_schema.table_constraints
     WHERE table_schema = 'public'
       AND table_name = 'copilot_lessons'
       AND constraint_name = 'copilot_lessons_workspace_id_fkey'
  ) THEN
    ALTER TABLE copilot_lessons
      ADD CONSTRAINT copilot_lessons_workspace_id_fkey
      FOREIGN KEY (workspace_id)
      REFERENCES workspaces(id)
      ON DELETE SET NULL;
  END IF;
END $$;


-- Self-register so the migration tracker knows this file applied.
INSERT INTO schema_migrations (filename, applied_at)
VALUES ('93_copilot_cuspide.sql', NOW())
ON CONFLICT (filename) DO NOTHING;
