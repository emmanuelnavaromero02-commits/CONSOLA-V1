-- Sprint v1.44.6 — real SMTP delivery for copilot drafts.
--
-- v1.44.2 kept delivery metadata inside ``metadata`` while the send
-- endpoint was a stub. The real sender needs durable, queryable runtime
-- fields so operations can distinguish drafted, delivered, and failed
-- messages without inspecting user-authored body content.

ALTER TABLE copilot_drafts
    ADD COLUMN IF NOT EXISTS sent_at TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS delivery_log JSONB NOT NULL DEFAULT '{}'::jsonb;

INSERT INTO schema_migrations (filename, applied_at)
VALUES ('57_copilot_draft_delivery.sql', NOW())
ON CONFLICT (filename) DO NOTHING;
