-- Sprint v1.42 — Indexes for copilot query patterns.
--
-- The "pending approvals" lookup is the only new hot path the v1.42
-- brain introduces that isn't already covered by an existing index:
--   * Listing user conversations  → idx_conversations_user_workspace (mig 38)
--   * Loading conversation history → idx_messages_conversation       (mig 38)
--   * Filtering audit by conversation → idx_audit_conversation       (mig 39)
--
-- Pending-approval lookup pattern (copilot.approve_pending_action):
--     SELECT tool_calls FROM conversation_messages
--      WHERE id = $1 AND conversation_id = $2
-- That's already a PK / FK seek so it's not what we index here. What we
-- want is the *list* of pending messages in a conversation (for a
-- future "show me pending approvals" panel and for monitoring) —
-- a partial index keeps this tiny in the steady state since the
-- vast majority of rows are NOT pending.

CREATE INDEX IF NOT EXISTS idx_messages_pending_approval
    ON conversation_messages (conversation_id, created_at DESC)
    WHERE tool_calls IS NOT NULL AND tool_results IS NULL;
