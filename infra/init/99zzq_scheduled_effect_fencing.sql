GRANT SELECT ON agent_schedule_runs TO omega_mcp_infra;
CREATE OR REPLACE FUNCTION assert_scheduled_effect_authority(
  p_schedule_run_id bigint,
  p_fencing_token bigint,
  p_tenant uuid,
  p_workspace uuid,
  p_agent uuid
) RETURNS void
LANGUAGE plpgsql SECURITY DEFINER
SET search_path=pg_catalog,public
AS $$
BEGIN
  PERFORM pg_advisory_xact_lock_shared(
    hashtextextended('agent_schedule_effect:' || p_schedule_run_id::text,0)
  );
  IF NOT EXISTS (
    SELECT 1 FROM agent_schedule_runs s
     WHERE s.id=p_schedule_run_id
       AND s.fencing_token=p_fencing_token
       AND s.status='running'
       AND s.lease_expires_at > clock_timestamp()
       AND s.tenant_id=p_tenant
       AND s.workspace_id=p_workspace
       AND s.agent_id=p_agent
  ) THEN
    RAISE EXCEPTION 'scheduled effect authority is stale' USING ERRCODE='40001';
  END IF;
END $$;
ALTER FUNCTION assert_scheduled_effect_authority(bigint,bigint,uuid,uuid,uuid)
  OWNER TO postgres;
REVOKE ALL ON FUNCTION assert_scheduled_effect_authority(bigint,bigint,uuid,uuid,uuid)
  FROM PUBLIC;
GRANT EXECUTE ON FUNCTION assert_scheduled_effect_authority(bigint,bigint,uuid,uuid,uuid)
  TO omega_mcp_infra,omega_console;
ALTER TABLE agent_schedule_runs ENABLE ROW LEVEL SECURITY;
ALTER TABLE agent_schedule_runs FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS agent_schedule_runs_mcp_effect_scope ON agent_schedule_runs;
CREATE POLICY agent_schedule_runs_mcp_effect_scope ON agent_schedule_runs
  FOR SELECT TO omega_mcp_infra
  USING (omega_rls_workspace_matches(tenant_id,workspace_id));
ALTER ROLE omega_mcp_infra NOBYPASSRLS;
INSERT INTO schema_migrations(filename,applied_at)
VALUES('99zzq_scheduled_effect_fencing.sql',NOW())
ON CONFLICT(filename) DO NOTHING;
