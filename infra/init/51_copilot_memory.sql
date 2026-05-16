-- Sprint v1.44.2 (Tarea G) — copilot memory: facts, preferences,
-- and per-conversation summaries.
--
-- The copilot's memory layer has three flavours:
--
--   user_facts                 — durable assertions about the user
--                                ("I prefer to be called by my first
--                                name", "my fiscal year starts in
--                                July"). Captured explicitly via
--                                /api/copilot/memory/fact or
--                                extracted from a turn by the LLM
--                                (next-session integration). Used to
--                                personalise prompts.
--   user_preferences           — strongly-typed key/value preferences
--                                (tone='formal', language='es',
--                                default_cartridge='replicon'). Type
--                                preserved as TEXT in this v1 — JSON
--                                schema validation lives in the
--                                application layer.
--   conversation_memory_summary — one row per conversation: a
--                                rolling summary the LLM consults
--                                when the conversation gets long
--                                enough to push older turns out of
--                                its context window. Generated
--                                lazily when a turn closes
--                                (next-session integration).
--
-- Idempotent end-to-end: CREATE TABLE IF NOT EXISTS, CREATE INDEX
-- IF NOT EXISTS, INSERT … ON CONFLICT DO NOTHING.

CREATE TABLE IF NOT EXISTS user_facts (
    id           BIGSERIAL    PRIMARY KEY,
    user_id      BIGINT       NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    fact         TEXT         NOT NULL,
    source       TEXT         NOT NULL DEFAULT 'explicit',  -- explicit | extracted
    confidence   REAL         NOT NULL DEFAULT 1.0,
    created_at   TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    -- v1.44.2: keep facts unique per user so the LLM extraction path
    -- can naively INSERT … ON CONFLICT DO NOTHING and stay idempotent
    -- when it re-observes the same fact across turns.
    UNIQUE (user_id, fact)
);

CREATE INDEX IF NOT EXISTS idx_user_facts_user
    ON user_facts (user_id, created_at DESC);

CREATE TABLE IF NOT EXISTS user_preferences (
    user_id      BIGINT       NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    pref_key     TEXT         NOT NULL,
    pref_value   TEXT         NOT NULL,
    updated_at   TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    PRIMARY KEY (user_id, pref_key)
);

CREATE TABLE IF NOT EXISTS conversation_memory_summary (
    conversation_id   UUID         PRIMARY KEY,
    -- The conversations table (mig 38) is the foreign-key parent.
    -- ON DELETE CASCADE so a deleted conversation takes its summary
    -- with it; manual cleanup is impossible to keep in sync.
    summary           TEXT         NOT NULL,
    token_count       INTEGER      NOT NULL DEFAULT 0,
    updated_at        TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

-- The FK on conversation_id is added separately so the migration is
-- safe on a DB where the conversations table hasn't been created
-- yet (e.g. a partial bootstrap). We attach it in a DO block guarded
-- by EXISTS so re-runs and missing-parent cases are no-ops.
DO $$
BEGIN
  IF EXISTS (
    SELECT 1 FROM information_schema.tables
     WHERE table_schema = 'public' AND table_name = 'conversations'
  ) AND NOT EXISTS (
    SELECT 1 FROM information_schema.table_constraints
     WHERE table_schema = 'public'
       AND table_name = 'conversation_memory_summary'
       AND constraint_type = 'FOREIGN KEY'
       AND constraint_name = 'conversation_memory_summary_conversation_id_fkey'
  ) THEN
    ALTER TABLE conversation_memory_summary
        ADD CONSTRAINT conversation_memory_summary_conversation_id_fkey
        FOREIGN KEY (conversation_id) REFERENCES conversations(id) ON DELETE CASCADE;
  END IF;
END $$;

-- Self-register.
INSERT INTO schema_migrations (filename, applied_at)
VALUES ('51_copilot_memory.sql', NOW())
ON CONFLICT (filename) DO NOTHING;
