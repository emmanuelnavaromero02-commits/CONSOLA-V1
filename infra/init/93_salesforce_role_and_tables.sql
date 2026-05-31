-- Salesforce cartridge — least-privilege DB role.
--
-- Same shape as the Replicon / SAP cartridge roles: a least-privilege
-- login role scoped to the operational catalog (cartridges, entity_config,
-- kb_config, watermarks, run logs) with explicit REVOKEs on every identity /
-- decisions / vault / audit table. The cartridge runs as
-- ``omega_cartridge_salesforce`` in production; it never connects as super.
--
-- Runs after 37_replicon_role_and_tables.sql, which owns the shared
-- operational tables — this role only receives GRANTs, never ownership.

DO $$
DECLARE
  pw TEXT := current_setting('app.omega_cartridge_salesforce_password', true);
BEGIN
  IF pw IS NULL OR pw = '' THEN
    RAISE EXCEPTION 'app.omega_cartridge_salesforce_password not set; refusing to create role with empty password';
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'omega_cartridge_salesforce') THEN
    EXECUTE format('CREATE ROLE omega_cartridge_salesforce LOGIN PASSWORD %L', pw);
  END IF;
END $$;

GRANT CONNECT ON DATABASE modecissions TO omega_cartridge_salesforce;
GRANT USAGE ON SCHEMA public TO omega_cartridge_salesforce;

-- The cartridge writes: extraction_runs (audit), entity_watermarks
-- (incremental state), kb_runs (knowledge bit history), jobs +
-- run_logs (the local job runner). It reads its own entity_config
-- + cartridges row plus the mcp_servers / mcp_custom_tools registry.
GRANT SELECT, INSERT, UPDATE ON
    cartridges, entity_config, kb_config,
    entity_watermarks, extraction_runs, kb_runs, jobs, run_logs
    TO omega_cartridge_salesforce;

GRANT SELECT ON
    mcp_servers, mcp_custom_tools
    TO omega_cartridge_salesforce;

GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO omega_cartridge_salesforce;

-- job_runner.ensure_schema() runs CREATE TABLE/INDEX IF NOT EXISTS on the
-- shared operational tables at startup. Postgres checks CREATE-on-schema even
-- when the table already exists, and CREATE INDEX needs ownership of `jobs`.
-- Like the SAP cartridges (see 46_sap_jobs_permissions.sql), grant CREATE on
-- the schema and membership in the shared NOLOGIN owner of `jobs` rather than
-- taking ownership directly (replicon already owns the tables at 37).
GRANT CREATE ON SCHEMA public TO omega_cartridge_salesforce;
DO $$
BEGIN
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'omega_cartridge_jobs_owner')
     AND EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'omega_cartridge_salesforce') THEN
    EXECUTE 'GRANT omega_cartridge_jobs_owner TO omega_cartridge_salesforce';
  END IF;
END $$;

-- Defense-in-depth: identity / auth / decisions / vault / audit
-- tables are hard-locked from omega_cartridge_salesforce.
DO $$
DECLARE
  hard_locked_tables CONSTANT text[] := ARRAY[
    'users', 'user_sessions', 'user_tokens', 'refresh_tokens',
    'tenants', 'workspaces', 'roles', 'user_workspace_roles',
    'decisions', 'decision_actions',
    'audit_events', 'login_attempts', 'vault_access_log',
    'vault_entries'
  ];
  tbl text;
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_roles WHERE rolname = 'omega_cartridge_salesforce'
  ) THEN
    RAISE NOTICE 'omega_cartridge_salesforce does not exist; skipping REVOKE block';
    RETURN;
  END IF;
  FOREACH tbl IN ARRAY hard_locked_tables LOOP
    IF EXISTS (
      SELECT 1 FROM pg_tables
      WHERE schemaname = 'public' AND tablename = tbl
    ) THEN
      EXECUTE format(
        'REVOKE ALL PRIVILEGES ON public.%I FROM omega_cartridge_salesforce',
        tbl
      );
    END IF;
  END LOOP;
END $$;
