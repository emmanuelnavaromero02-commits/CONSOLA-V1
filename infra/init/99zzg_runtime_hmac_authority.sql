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
    CHECK (audience IN ('refinement','mcp-infra')),
  ADD CONSTRAINT runtime_hmac_nonces_purpose_check
    CHECK (purpose IN (
      'refinement.mcp.materialize','mcp.pipeline_run_save','mcp.scheduled_effect'
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
INSERT INTO schema_migrations(filename,applied_at)
VALUES('99zzg_runtime_hmac_authority.sql',NOW())
ON CONFLICT(filename) DO NOTHING;
