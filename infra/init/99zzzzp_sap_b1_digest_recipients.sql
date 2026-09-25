CREATE TABLE IF NOT EXISTS sap_b1_digest_recipients (
    tenant_id       UUID NOT NULL,
    workspace_id    UUID NOT NULL,
    email           TEXT NOT NULL,
    added_by        BIGINT,
    added_at        TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY (tenant_id, workspace_id, email),
    CONSTRAINT sap_b1_digest_recipients_scope_fk
        FOREIGN KEY (tenant_id, workspace_id)
        REFERENCES workspaces(tenant_id, id) ON DELETE CASCADE,
    CONSTRAINT sap_b1_digest_recipients_email_check
        CHECK (length(email) BETWEEN 6 AND 254
               AND email = lower(email)
               AND email ~ '^[^@[:space:],;=<>"'']+@[^@[:space:],;=<>"'']+\.[^@[:space:],;=<>"'']+$')
);

ALTER TABLE sap_b1_digest_recipients ENABLE ROW LEVEL SECURITY;
ALTER TABLE sap_b1_digest_recipients FORCE ROW LEVEL SECURITY;

DO $sap_b1_digest_recipients_policy$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'omega_console') THEN
        RAISE NOTICE 'sap_b1_digest_recipients: omega_console absent, policy skipped';
        RETURN;
    END IF;
    EXECUTE 'DROP POLICY IF EXISTS sap_b1_digest_recipients_workspace_scope ON sap_b1_digest_recipients';
    EXECUTE 'CREATE POLICY sap_b1_digest_recipients_workspace_scope ON sap_b1_digest_recipients'
            ' FOR ALL TO omega_console'
            ' USING (omega_rls_workspace_matches(tenant_id, workspace_id))'
            ' WITH CHECK (omega_rls_workspace_matches(tenant_id, workspace_id))';
    EXECUTE 'GRANT SELECT, INSERT, DELETE ON sap_b1_digest_recipients TO omega_console';
    EXECUTE 'REVOKE UPDATE, TRUNCATE ON sap_b1_digest_recipients FROM omega_console';
END
$sap_b1_digest_recipients_policy$;

REVOKE ALL ON sap_b1_digest_recipients FROM PUBLIC;

INSERT INTO schema_migrations (filename, applied_at)
VALUES ('99zzzzp_sap_b1_digest_recipients.sql', NOW())
ON CONFLICT (filename) DO NOTHING;
