-- Sprint v1.44.2 (Tarea H) — copilot drafts (intelligent writing).
--
-- One row per draft. The user requests "redact a follow-up email to
-- Acme about the missing invoice"; the copilot LLM produces a draft
-- (next-session LLM integration) and persists it here so the user
-- can review, regenerate, edit, and ultimately send through an
-- approved channel (also next session).
--
-- ``status`` lifecycle: draft → sent | discarded. Drafts are
-- explicitly user-scoped — no shared drafts.

CREATE TABLE IF NOT EXISTS copilot_drafts (
    id           UUID         PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id      BIGINT       NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    kind         TEXT         NOT NULL,
        -- one of: email | memo | note | report
    title        TEXT         NOT NULL DEFAULT '',
    body         TEXT         NOT NULL,
    tone         TEXT         NOT NULL DEFAULT 'neutral',
        -- one of: formal | neutral | friendly | urgent
    status       TEXT         NOT NULL DEFAULT 'draft',
        -- one of: draft | sent | discarded
    metadata     JSONB        NOT NULL DEFAULT '{}'::jsonb,
        -- LLM call settings, regeneration count, recipient suggestion, etc.
    -- v1.44.2 (Claude H7 follow-through): when a draft is sent, we
    -- store the destination + send_status as JSONB rather than
    -- adding columns now — keeps the schema minimal until v1.45
    -- formalises the channels.
    created_at   TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    updated_at   TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_copilot_drafts_user_updated
    ON copilot_drafts (user_id, updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_copilot_drafts_status
    ON copilot_drafts (status)
 WHERE status != 'discarded';

-- Self-register.
INSERT INTO schema_migrations (filename, applied_at)
VALUES ('52_copilot_drafts.sql', NOW())
ON CONFLICT (filename) DO NOTHING;
