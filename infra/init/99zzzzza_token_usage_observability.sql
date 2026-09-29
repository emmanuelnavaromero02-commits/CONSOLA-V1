-- v1.45.260 — token_usage observability: provider latency and calling surface.

ALTER TABLE token_usage
    ADD COLUMN IF NOT EXISTS duration_ms INTEGER;

ALTER TABLE token_usage
    ADD COLUMN IF NOT EXISTS surface TEXT;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'chk_token_usage_surface'
    ) THEN
        ALTER TABLE token_usage
            ADD CONSTRAINT chk_token_usage_surface
            CHECK (
                surface IS NULL
                OR surface IN ('copilot', 'studio', 'rag', 'catalog', 'workspace', 'other')
            )
            NOT VALID;
    END IF;
END $$;

ALTER TABLE token_usage VALIDATE CONSTRAINT chk_token_usage_surface;

CREATE INDEX IF NOT EXISTS idx_audit_events_copilot_sends
    ON audit_events(action)
    WHERE action = 'copilot.message.send';

INSERT INTO schema_migrations (filename, applied_at)
VALUES ('99zzzzza_token_usage_observability.sql', NOW())
ON CONFLICT (filename) DO NOTHING;
