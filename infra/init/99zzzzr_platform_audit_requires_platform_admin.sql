-- Platform audit context requires no workspace scope and app.platform_admin = 'true'.

CREATE OR REPLACE FUNCTION omega_20b_platform_audit_context()
RETURNS boolean
LANGUAGE sql
STABLE
AS $$
    SELECT NULLIF(current_setting('app.workspace_id', true), '') IS NULL
       AND COALESCE(current_setting('app.platform_admin', true), '') = 'true'
$$;

INSERT INTO schema_migrations (filename, applied_at)
VALUES ('99zzzzr_platform_audit_requires_platform_admin.sql', NOW())
ON CONFLICT (filename) DO NOTHING;
