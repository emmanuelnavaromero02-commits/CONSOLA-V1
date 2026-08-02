CREATE TABLE IF NOT EXISTS runtime_hmac_nonces (
  jti text PRIMARY KEY CHECK (jti ~ '^[0-9a-f]{64}$'),
  tenant_id uuid REFERENCES tenants(id) ON DELETE CASCADE,
  workspace_id uuid REFERENCES workspaces(id) ON DELETE CASCADE,
  run_id text NOT NULL CHECK (length(run_id) BETWEEN 1 AND 250),
  audience text NOT NULL,
  purpose text NOT NULL,
  body_digest text NOT NULL CHECK (body_digest ~ '^[0-9a-f]{64}$'),
  signed_at timestamptz NOT NULL,
  expires_at timestamptz NOT NULL,
  consumed_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  dag_id text,
  cartridge_id text,
  schedule_run_id bigint,
  fencing_token bigint,
  CHECK (expires_at > signed_at)
);
ALTER TABLE runtime_hmac_nonces
  ALTER COLUMN tenant_id DROP NOT NULL,
  ALTER COLUMN workspace_id DROP NOT NULL,
  ADD COLUMN IF NOT EXISTS dag_id text,
  ADD COLUMN IF NOT EXISTS cartridge_id text,
  ADD COLUMN IF NOT EXISTS schedule_run_id bigint,
  ADD COLUMN IF NOT EXISTS fencing_token bigint;
ALTER TABLE runtime_hmac_nonces
  DROP CONSTRAINT IF EXISTS runtime_hmac_nonces_audience_check,
  DROP CONSTRAINT IF EXISTS runtime_hmac_nonces_purpose_check;
ALTER TABLE runtime_hmac_nonces
  ADD CONSTRAINT runtime_hmac_nonces_audience_check
    CHECK (audience IN ('refinement','mcp-infra','airflow')),
  ADD CONSTRAINT runtime_hmac_nonces_purpose_check
    CHECK (purpose IN (
      'refinement.mcp.materialize','mcp.pipeline_run_save','mcp.scheduled_effect',
      'dataset_refresh.admit'
    ));
DO $$ BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint
     WHERE conrelid='runtime_hmac_nonces'::regclass
       AND conname='runtime_hmac_nonces_scope_check'
  ) THEN
    ALTER TABLE runtime_hmac_nonces
      ADD CONSTRAINT runtime_hmac_nonces_scope_check CHECK (
        (purpose='mcp.pipeline_run_save' AND cartridge_id='platform'
          AND tenant_id IS NULL AND workspace_id IS NULL)
        OR (tenant_id IS NOT NULL AND workspace_id IS NOT NULL)
      );
  END IF;
END $$;
DROP POLICY IF EXISTS runtime_hmac_nonces_mcp_scope ON runtime_hmac_nonces;
CREATE POLICY runtime_hmac_nonces_mcp_scope ON runtime_hmac_nonces
  FOR INSERT TO omega_mcp_infra
  WITH CHECK (
    (purpose='mcp.pipeline_run_save' AND cartridge_id='platform'
      AND tenant_id IS NULL AND workspace_id IS NULL)
    OR omega_rls_workspace_matches(tenant_id,workspace_id)
  );
GRANT INSERT ON runtime_hmac_nonces TO omega_mcp_infra;
REVOKE SELECT,UPDATE,DELETE,TRUNCATE ON runtime_hmac_nonces FROM omega_mcp_infra;
ALTER ROLE omega_mcp_infra NOBYPASSRLS;

CREATE OR REPLACE FUNCTION consume_dataset_refresh_admission(
  p_jti text,p_tenant uuid,p_workspace uuid,p_run_id text,
  p_cartridge text,p_body_digest text,p_signed_at bigint,p_expires_at bigint
) RETURNS boolean LANGUAGE plpgsql SECURITY DEFINER
SET search_path=pg_catalog,public AS $$
BEGIN
  IF EXISTS(SELECT 1 FROM runtime_hmac_nonces n WHERE n.jti=p_jti) THEN
    IF EXISTS(SELECT 1 FROM runtime_hmac_nonces n WHERE n.jti=p_jti
      AND n.tenant_id=p_tenant AND n.workspace_id=p_workspace
      AND n.run_id=p_run_id AND n.audience='airflow'
      AND n.purpose='dataset_refresh.admit' AND n.body_digest=p_body_digest
      AND n.signed_at=to_timestamp(p_signed_at)
      AND n.expires_at=to_timestamp(p_expires_at)
      AND n.dag_id='dataset_refresh_chain' AND n.cartridge_id=p_cartridge) THEN
      RETURN true;
    END IF;
    RAISE EXCEPTION 'dataset refresh admission replay mismatch'
      USING ERRCODE='23505';
  END IF;
  IF session_user<>'omega_airflow_dag'
     OR p_jti !~ '^[0-9a-f]{64}$'
     OR p_body_digest !~ '^[0-9a-f]{64}$'
     OR COALESCE(p_run_id,'')='' OR COALESCE(p_cartridge,'')=''
     OR p_expires_at-p_signed_at NOT IN (300,86400)
     OR to_timestamp(p_expires_at)<=clock_timestamp()
     OR NOT EXISTS(SELECT 1 FROM workspaces w
                    WHERE w.id=p_workspace AND w.tenant_id=p_tenant) THEN
    RAISE EXCEPTION 'dataset refresh admission is invalid' USING ERRCODE='42501';
  END IF;
  INSERT INTO runtime_hmac_nonces(
    jti,tenant_id,workspace_id,run_id,audience,purpose,body_digest,
    signed_at,expires_at,dag_id,cartridge_id
  ) VALUES(
    p_jti,p_tenant,p_workspace,p_run_id,'airflow','dataset_refresh.admit',
    p_body_digest,to_timestamp(p_signed_at),to_timestamp(p_expires_at),
    'dataset_refresh_chain',p_cartridge
  ) ON CONFLICT(jti) DO NOTHING;
  IF NOT EXISTS(SELECT 1 FROM runtime_hmac_nonces n WHERE n.jti=p_jti
      AND n.tenant_id=p_tenant AND n.workspace_id=p_workspace
      AND n.run_id=p_run_id AND n.audience='airflow'
      AND n.purpose='dataset_refresh.admit' AND n.body_digest=p_body_digest
      AND n.signed_at=to_timestamp(p_signed_at)
      AND n.expires_at=to_timestamp(p_expires_at)
      AND n.dag_id='dataset_refresh_chain' AND n.cartridge_id=p_cartridge) THEN
    RAISE EXCEPTION 'dataset refresh admission replay mismatch'
      USING ERRCODE='23505';
  END IF;
  RETURN true;
END $$;
ALTER FUNCTION consume_dataset_refresh_admission(
  text,uuid,uuid,text,text,text,bigint,bigint
) OWNER TO postgres;
REVOKE ALL ON FUNCTION consume_dataset_refresh_admission(
  text,uuid,uuid,text,text,text,bigint,bigint
) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION consume_dataset_refresh_admission(
  text,uuid,uuid,text,text,text,bigint,bigint
) TO omega_airflow_dag;
ALTER ROLE omega_airflow_dag NOBYPASSRLS;
INSERT INTO schema_migrations(filename,applied_at)
VALUES('99zzo_runtime_hmac_authority.sql',NOW())
ON CONFLICT(filename) DO NOTHING;
