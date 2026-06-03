-- v1.45.2: Vault entries can be scoped per tenant/workspace.
--
-- Global rows remain for platform/bootstrap compatibility, but user-facing
-- Vault reads/writes with a signed security_context use the scoped key. This
-- prevents Tenant A from resolving Tenant B's `connections/<cartridge>/default`.

ALTER TABLE vault_entries
    ADD COLUMN IF NOT EXISTS tenant_id UUID REFERENCES tenants(id) ON DELETE CASCADE;

ALTER TABLE vault_entries
    ADD COLUMN IF NOT EXISTS workspace_id UUID REFERENCES workspaces(id) ON DELETE CASCADE;

DO $$
BEGIN
    IF EXISTS (
        SELECT 1
          FROM pg_constraint
         WHERE conrelid = 'vault_entries'::regclass
           AND conname = 'vault_entries_pkey'
    ) THEN
        ALTER TABLE vault_entries DROP CONSTRAINT vault_entries_pkey;
    END IF;
END $$;

CREATE UNIQUE INDEX IF NOT EXISTS vault_entries_global_key_idx
    ON vault_entries(scope, cartridge, key)
    WHERE tenant_id IS NULL AND workspace_id IS NULL;

CREATE UNIQUE INDEX IF NOT EXISTS vault_entries_workspace_key_idx
    ON vault_entries(tenant_id, workspace_id, scope, cartridge, key)
    WHERE tenant_id IS NOT NULL AND workspace_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS vault_entries_workspace_lookup_idx
    ON vault_entries(workspace_id, scope, cartridge, key);

ALTER TABLE vault_access_log
    ADD COLUMN IF NOT EXISTS tenant_id UUID;

ALTER TABLE vault_access_log
    ADD COLUMN IF NOT EXISTS workspace_id UUID;

CREATE INDEX IF NOT EXISTS idx_vault_access_log_workspace_timestamp
    ON vault_access_log(workspace_id, timestamp DESC);

INSERT INTO schema_migrations (filename, applied_at)
VALUES ('99c_vault_workspace_scope.sql', NOW())
ON CONFLICT (filename) DO NOTHING;
