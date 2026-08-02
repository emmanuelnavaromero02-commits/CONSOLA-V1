-- Durable one-use replay ledger for Airflow -> Refinement HMAC v2 envelopes.

CREATE TABLE IF NOT EXISTS runtime_hmac_nonces (
    jti             TEXT PRIMARY KEY CHECK (jti ~ '^[0-9a-f]{64}$'),
    tenant_id       UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    workspace_id    UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    run_id          TEXT NOT NULL CHECK (length(run_id) BETWEEN 1 AND 250),
    audience        TEXT NOT NULL CHECK (audience = 'refinement'),
    purpose         TEXT NOT NULL CHECK (purpose = 'refinement.mcp.materialize'),
    body_digest     TEXT NOT NULL CHECK (body_digest ~ '^[0-9a-f]{64}$'),
    signed_at       TIMESTAMPTZ NOT NULL,
    expires_at      TIMESTAMPTZ NOT NULL,
    consumed_at     TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    CHECK (expires_at > signed_at)
);

CREATE INDEX IF NOT EXISTS runtime_hmac_nonces_expiry_idx
  ON runtime_hmac_nonces (expires_at);
CREATE INDEX IF NOT EXISTS runtime_hmac_nonces_scope_run_idx
  ON runtime_hmac_nonces (tenant_id, workspace_id, run_id);

GRANT SELECT, INSERT ON runtime_hmac_nonces TO omega_refinement;
REVOKE UPDATE, DELETE, TRUNCATE ON runtime_hmac_nonces FROM omega_refinement;

ALTER TABLE runtime_hmac_nonces ENABLE ROW LEVEL SECURITY;
ALTER TABLE runtime_hmac_nonces FORCE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS runtime_hmac_nonces_refinement_scope ON runtime_hmac_nonces;
CREATE POLICY runtime_hmac_nonces_refinement_scope ON runtime_hmac_nonces
  FOR ALL TO omega_refinement
  USING (omega_rls_workspace_matches(tenant_id, workspace_id))
  WITH CHECK (omega_rls_workspace_matches(tenant_id, workspace_id));

INSERT INTO schema_migrations(filename, applied_at)
VALUES ('99zzl_runtime_hmac_nonces.sql', NOW())
ON CONFLICT (filename) DO NOTHING;
