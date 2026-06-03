-- v1.44.14 — token usage must be tenant/workspace scoped.
--
-- Existing historical rows remain unscoped and are visible only to platform
-- admins. New LLM calls record user_id, tenant_id and workspace_id so customer
-- tenants only see their own consumption.

ALTER TABLE token_usage
    ADD COLUMN IF NOT EXISTS user_id BIGINT;

ALTER TABLE token_usage
    ADD COLUMN IF NOT EXISTS tenant_id UUID;

ALTER TABLE token_usage
    ADD COLUMN IF NOT EXISTS workspace_id UUID;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'fk_token_usage_user'
    ) THEN
        ALTER TABLE token_usage
            ADD CONSTRAINT fk_token_usage_user
            FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE SET NULL;
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'fk_token_usage_tenant'
    ) THEN
        ALTER TABLE token_usage
            ADD CONSTRAINT fk_token_usage_tenant
            FOREIGN KEY (tenant_id) REFERENCES tenants(id) ON DELETE SET NULL;
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'fk_token_usage_workspace'
    ) THEN
        ALTER TABLE token_usage
            ADD CONSTRAINT fk_token_usage_workspace
            FOREIGN KEY (workspace_id) REFERENCES workspaces(id) ON DELETE SET NULL;
    END IF;
END $$;

CREATE INDEX IF NOT EXISTS idx_token_usage_workspace_ts
    ON token_usage(workspace_id, ts DESC);

CREATE INDEX IF NOT EXISTS idx_token_usage_tenant_ts
    ON token_usage(tenant_id, ts DESC);

INSERT INTO schema_migrations (filename, applied_at)
VALUES ('97_token_usage_tenant_scope.sql', NOW())
ON CONFLICT (filename) DO NOTHING;
