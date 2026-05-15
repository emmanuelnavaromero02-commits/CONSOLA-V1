-- Sprint v1.40 — Replicon cartridge restored from original ZIP.
--
-- Same shape as SAP cartridge roles introduced in v1.38: a
-- least-privilege login role scoped to the operational catalog
-- (cartridges, entity_config, kb_config, watermarks, run logs)
-- with explicit REVOKEs on every identity / decisions / vault /
-- audit table. The cartridge runs as ``omega_cartridge_replicon``
-- in production; legacy postgres-superuser access stays in the
-- bootstrap path only (the cartridge itself never connects as super).

DO $$
DECLARE
  pw TEXT := current_setting('app.omega_cartridge_replicon_password', true);
BEGIN
  IF pw IS NULL OR pw = '' THEN
    RAISE EXCEPTION 'app.omega_cartridge_replicon_password not set; refusing to create role with empty password';
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'omega_cartridge_replicon') THEN
    EXECUTE format('CREATE ROLE omega_cartridge_replicon LOGIN PASSWORD %L', pw);
  END IF;
END $$;

GRANT CONNECT ON DATABASE modecissions TO omega_cartridge_replicon;
GRANT USAGE ON SCHEMA public TO omega_cartridge_replicon;

-- The cartridge writes: extraction_runs (audit), entity_watermarks
-- (incremental state), kb_runs (knowledge bit history), jobs +
-- run_logs (the local job runner). It reads its own entity_config
-- + cartridges row plus the mcp_servers / mcp_custom_tools registry.
GRANT SELECT, INSERT, UPDATE ON
    cartridges, entity_config, kb_config,
    entity_watermarks, extraction_runs, kb_runs, jobs, run_logs
    TO omega_cartridge_replicon;

GRANT SELECT ON
    mcp_servers, mcp_custom_tools
    TO omega_cartridge_replicon;

GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO omega_cartridge_replicon;

-- Defense-in-depth: identity / auth / decisions / vault / audit
-- tables are hard-locked from omega_cartridge_replicon. Same list
-- v1.38 used for the SAP cartridge roles.
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
    SELECT 1 FROM pg_roles WHERE rolname = 'omega_cartridge_replicon'
  ) THEN
    RAISE NOTICE 'omega_cartridge_replicon does not exist; skipping REVOKE block';
    RETURN;
  END IF;
  FOREACH tbl IN ARRAY hard_locked_tables LOOP
    IF EXISTS (
      SELECT 1 FROM pg_tables
      WHERE schemaname = 'public' AND tablename = tbl
    ) THEN
      EXECUTE format(
        'REVOKE ALL PRIVILEGES ON public.%I FROM omega_cartridge_replicon',
        tbl
      );
    END IF;
  END LOOP;
END $$;

-- ─────────────────────────────────────────────────────────────
-- v1.40.3: cartridge owns its operational tables.
--
-- Previously these were applied manually in dev. Codifying so
-- a fresh `docker compose up -v` produces an identical environment
-- without manual GRANT/ALTER steps.
-- ─────────────────────────────────────────────────────────────

GRANT CREATE ON SCHEMA public TO omega_cartridge_replicon;

DO $$
DECLARE
  operational_tables CONSTANT text[] := ARRAY[
    'jobs', 'run_logs', 'entity_watermarks',
    'extraction_runs', 'kb_runs'
  ];
  tbl text;
BEGIN
  FOREACH tbl IN ARRAY operational_tables LOOP
    IF EXISTS (
      SELECT 1 FROM pg_tables
      WHERE schemaname = 'public' AND tablename = tbl
    ) THEN
      EXECUTE format(
        'ALTER TABLE public.%I OWNER TO omega_cartridge_replicon',
        tbl
      );
    END IF;
  END LOOP;
END $$;
