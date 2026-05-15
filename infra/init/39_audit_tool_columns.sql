-- Sprint v1.41.0 — tornillo para el copiloto IA central (v1.42).
--
-- Adds tool-call columns to audit_events. They will be populated
-- by the copilot in v1.42 when it executes tools on behalf of users.
-- For now they remain NULL on all existing rows.

ALTER TABLE audit_events
    ADD COLUMN IF NOT EXISTS tool_name TEXT,
    ADD COLUMN IF NOT EXISTS tool_args JSONB,
    ADD COLUMN IF NOT EXISTS tool_result_status TEXT,
    ADD COLUMN IF NOT EXISTS risk_level TEXT,
    ADD COLUMN IF NOT EXISTS conversation_id UUID
        REFERENCES conversations(id) ON DELETE SET NULL;

CREATE INDEX IF NOT EXISTS idx_audit_tool_name
    ON audit_events(tool_name) WHERE tool_name IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_audit_conversation
    ON audit_events(conversation_id) WHERE conversation_id IS NOT NULL;
