GRANT SELECT ON agent_schedule_runs TO omega_mcp_infra;
ALTER TABLE agent_schedule_runs ENABLE ROW LEVEL SECURITY;
ALTER TABLE agent_schedule_runs FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS agent_schedule_runs_mcp_effect_scope ON agent_schedule_runs;
CREATE POLICY agent_schedule_runs_mcp_effect_scope ON agent_schedule_runs
  FOR SELECT TO omega_mcp_infra
  USING (omega_rls_workspace_matches(tenant_id,workspace_id));
ALTER ROLE omega_mcp_infra NOBYPASSRLS;
INSERT INTO schema_migrations(filename,applied_at)
VALUES('99zzh_scheduled_effect_fencing.sql',NOW())
ON CONFLICT(filename) DO NOTHING;
