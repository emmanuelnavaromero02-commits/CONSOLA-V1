-- v1.45.2 — Workspace-scoped agents.
--
-- Existing seed agents remain global templates (workspace_id IS NULL).
-- Tenant/workspace users may create and manage only agents scoped to their
-- workspace; global seed agents stay read-only outside platform admins.
--
-- Keep this after 17_agents.sql in lexicographic Docker init order. A plain
-- "100_" prefix sorts before "10_", so this file intentionally uses "99a_".

ALTER TABLE agents
  ADD COLUMN IF NOT EXISTS tenant_id UUID REFERENCES tenants(id) ON DELETE CASCADE;

ALTER TABLE agents
  ADD COLUMN IF NOT EXISTS workspace_id UUID REFERENCES workspaces(id) ON DELETE CASCADE;

ALTER TABLE agent_runs
  ADD COLUMN IF NOT EXISTS tenant_id UUID REFERENCES tenants(id) ON DELETE SET NULL;

ALTER TABLE agent_runs
  ADD COLUMN IF NOT EXISTS workspace_id UUID REFERENCES workspaces(id) ON DELETE SET NULL;

DO $$
BEGIN
  IF EXISTS (
      SELECT 1
        FROM pg_constraint
       WHERE conname = 'agents_cartridge_id_slug_key'
         AND conrelid = 'agents'::regclass
  ) THEN
    ALTER TABLE agents DROP CONSTRAINT agents_cartridge_id_slug_key;
  END IF;
END $$;

CREATE UNIQUE INDEX IF NOT EXISTS agents_global_cartridge_slug_uidx
  ON agents (cartridge_id, slug)
  WHERE workspace_id IS NULL;

CREATE UNIQUE INDEX IF NOT EXISTS agents_workspace_cartridge_slug_uidx
  ON agents (workspace_id, cartridge_id, slug)
  WHERE workspace_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS agents_workspace_idx
  ON agents (workspace_id, cartridge_id, is_active);

CREATE INDEX IF NOT EXISTS agents_tenant_idx
  ON agents (tenant_id, workspace_id);

CREATE INDEX IF NOT EXISTS agent_runs_workspace_idx
  ON agent_runs (workspace_id, agent_id, started_at DESC);

CREATE INDEX IF NOT EXISTS agent_runs_tenant_idx
  ON agent_runs (tenant_id, workspace_id);

INSERT INTO schema_migrations(filename, applied_at)
VALUES ('99a_agent_workspace_scope.sql', NOW())
ON CONFLICT (filename) DO NOTHING;
