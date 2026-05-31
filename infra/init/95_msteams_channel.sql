-- Microsoft Teams channel — conversation continuity mapping.
--
-- Additive and idempotent (CREATE TABLE IF NOT EXISTS). The Teams channel is
-- disabled by default (MSTEAMS_ENABLED=false), so this table is untouched
-- until an operator turns the channel on. It maps a stable Teams conversation
-- id + console user to a console copilot conversation so multi-turn chat keeps
-- context. No business data lives here — Teams is transport only.

CREATE TABLE IF NOT EXISTS msteams_conversations (
    id                     BIGSERIAL PRIMARY KEY,
    teams_conversation_id  TEXT   NOT NULL,
    console_user_id        BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    conversation_id        UUID   NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
    tenant_id              TEXT,
    mode                   TEXT   NOT NULL DEFAULT 'dm',
    created_at             TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at             TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    -- The UNIQUE constraint creates an implicit btree on
    -- (teams_conversation_id, console_user_id) — the EXACT shape of the only
    -- lookup the channel runs (service._ensure_conversation). No additional
    -- index is needed; a duplicate would just amplify writes.
    UNIQUE (teams_conversation_id, console_user_id)
);
