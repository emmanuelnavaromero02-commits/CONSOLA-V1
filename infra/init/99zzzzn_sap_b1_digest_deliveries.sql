CREATE TABLE IF NOT EXISTS sap_b1_digest_deliveries (
    tenant_id       UUID NOT NULL,
    workspace_id    UUID NOT NULL,
    local_date      DATE NOT NULL,
    status          TEXT NOT NULL DEFAULT 'sending',
    recipients      INTEGER NOT NULL DEFAULT 0,
    delivered       INTEGER NOT NULL DEFAULT 0,
    agent_run_id    TEXT,
    claimed_at      TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    finished_at     TIMESTAMPTZ,
    PRIMARY KEY (tenant_id, workspace_id, local_date),
    CONSTRAINT sap_b1_digest_deliveries_scope_fk
        FOREIGN KEY (tenant_id, workspace_id)
        REFERENCES workspaces(tenant_id, id) ON DELETE CASCADE,
    CONSTRAINT sap_b1_digest_deliveries_status_check
        CHECK (status IN ('sending', 'sent', 'failed')),
    CONSTRAINT sap_b1_digest_deliveries_counts_check
        CHECK (recipients BETWEEN 0 AND 20 AND delivered BETWEEN 0 AND recipients),
    CONSTRAINT sap_b1_digest_deliveries_run_check
        CHECK (agent_run_id IS NULL OR length(agent_run_id) BETWEEN 1 AND 200)
);

ALTER TABLE sap_b1_digest_deliveries ENABLE ROW LEVEL SECURITY;
ALTER TABLE sap_b1_digest_deliveries FORCE ROW LEVEL SECURITY;

DO $sap_b1_digest_deliveries_policy$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'omega_console') THEN
        RAISE NOTICE 'sap_b1_digest_deliveries: omega_console absent, policy skipped';
        RETURN;
    END IF;
    EXECUTE 'DROP POLICY IF EXISTS sap_b1_digest_deliveries_workspace_scope ON sap_b1_digest_deliveries';
    EXECUTE 'CREATE POLICY sap_b1_digest_deliveries_workspace_scope ON sap_b1_digest_deliveries'
            ' FOR ALL TO omega_console'
            ' USING (omega_rls_workspace_matches(tenant_id, workspace_id))'
            ' WITH CHECK (omega_rls_workspace_matches(tenant_id, workspace_id))';
    EXECUTE 'REVOKE DELETE, TRUNCATE ON sap_b1_digest_deliveries FROM omega_console';
    EXECUTE 'GRANT SELECT, INSERT, UPDATE ON sap_b1_digest_deliveries TO omega_console';
END
$sap_b1_digest_deliveries_policy$;

REVOKE ALL ON sap_b1_digest_deliveries FROM PUBLIC;

INSERT INTO schema_migrations (filename, applied_at)
VALUES ('99zzzzn_sap_b1_digest_deliveries.sql', NOW())
ON CONFLICT (filename) DO NOTHING;
