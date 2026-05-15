-- Sprint v1.36 — lockdown omega_mcp_infra (audit B4 P0).
--
-- Pre-v1.36, infra/init/25_service_roles.sql granted SELECT on the
-- following tables to omega_mcp_infra even though no MCP tool reads
-- them (grep "FROM users|tenants|decisions|roles|workspaces" in
-- mcp-infra/app returns zero hits):
--
--   users, tenants, decisions, roles, workspaces
--
-- Combined with postgres_execute_query (which lets the caller send
-- an arbitrary SELECT), the GRANT was a path to ``users.password_hash``
-- exfiltration if a non-admin ever reached /api/mcp/invoke (audit B2,
-- fixed in v1.34) — or if a future bug bypassed that guard.
--
-- v1.36 removes those table-level reads. v1.25 (this file) also adds
-- the related identity/auth/audit tables that the v1.20 GRANT *did*
-- not name explicitly but that ``GRANT ON ALL TABLES`` in an
-- omega_console-shaped extension might add later. The REVOKEs are
-- declared with IF EXISTS / DO blocks so the migration is safe to
-- replay on:
--   1. a fresh DB where 25_service_roles.sql just ran (no-op REVOKE),
--   2. a v1.19-v1.35 DB where the old GRANT is still in pg_catalog,
--   3. a DB that's missing some of these tables (older / partial
--      install) — we skip the table instead of erroring.
--
-- IMPORTANT: vault_entries is already locked down by v1.19; we don't
-- need to re-revoke it here.

DO $$
DECLARE
  sensitive_tables CONSTANT text[] := ARRAY[
    'users',
    'user_sessions',
    'user_tokens',
    'refresh_tokens',
    'tenants',
    'workspaces',
    'roles',
    'user_workspace_roles',
    'decisions',
    'decision_actions',
    'audit_events',
    'login_attempts',
    'vault_access_log'
  ];
  tbl text;
BEGIN
  -- omega_mcp_infra must not exist on a fresh DB before
  -- 25_service_roles.sql runs; skip the revoke loop entirely in that
  -- case so this migration is safe to apply on a brand-new volume.
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'omega_mcp_infra') THEN
    RAISE NOTICE 'omega_mcp_infra role does not exist yet; skipping lockdown';
    RETURN;
  END IF;

  FOREACH tbl IN ARRAY sensitive_tables LOOP
    IF EXISTS (
      SELECT 1
        FROM pg_tables
       WHERE schemaname = 'public'
         AND tablename = tbl
    ) THEN
      EXECUTE format('REVOKE ALL PRIVILEGES ON public.%I FROM omega_mcp_infra', tbl);
      RAISE NOTICE 'omega_mcp_infra: revoked all on %', tbl;
    ELSE
      RAISE NOTICE 'omega_mcp_infra: table % not present, skipping', tbl;
    END IF;
  END LOOP;
END $$;
