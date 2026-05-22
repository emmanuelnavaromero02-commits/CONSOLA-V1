-- SaaS scoping for pipeline run history.
--
-- Airflow, Studio, Workspace and support views use pipeline_runs to explain
-- extraction/materialization state. These columns let every reader filter by
-- tenant/workspace instead of relying only on cartridge/entity names.

ALTER TABLE pipeline_runs
    ADD COLUMN IF NOT EXISTS tenant_id UUID REFERENCES tenants(id) ON DELETE SET NULL,
    ADD COLUMN IF NOT EXISTS workspace_id UUID REFERENCES workspaces(id) ON DELETE SET NULL;

CREATE INDEX IF NOT EXISTS idx_pipeline_runs_workspace_cartridge
    ON pipeline_runs(workspace_id, cartridge_id, entity, started_at DESC);

CREATE INDEX IF NOT EXISTS idx_pipeline_runs_tenant_workspace_status
    ON pipeline_runs(tenant_id, workspace_id, status, started_at DESC);

GRANT SELECT ON pipeline_runs TO omega_workspace;
GRANT SELECT, INSERT, UPDATE ON pipeline_runs TO omega_console;
GRANT SELECT, INSERT, UPDATE ON pipeline_runs TO omega_airflow_dag;

INSERT INTO schema_migrations (filename, applied_at)
VALUES ('76_pipeline_runs_scope.sql', NOW())
ON CONFLICT (filename) DO NOTHING;
