-- Legacy unscoped rows and platform templates stay reachable only from sessions
-- that carry no workspace scope AND the verified platform-admin flag the
-- services set after authorizing the caller (app.platform_admin = 'true').
-- An unscoped session without the flag sees no legacy rows and cannot write
-- platform templates. Same signature, language and volatility as 99w.

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
