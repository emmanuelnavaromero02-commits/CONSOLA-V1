
DO $$
DECLARE
  pw TEXT := current_setting('app.omega_cartridge_sap_b1_password', true);
BEGIN
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'omega_cartridge_sap_b1') THEN
    RAISE NOTICE 'omega_cartridge_sap_b1 already exists; grants below are re-applied';
    RETURN;
  END IF;
  IF pw IS NULL OR pw = '' THEN
    RAISE WARNING 'app.omega_cartridge_sap_b1_password not set; omega_cartridge_sap_b1 NOT created. Set OMEGA_CARTRIDGE_SAP_B1_PASSWORD and re-run this file before enabling the sap-b1 service.';
    RETURN;
  END IF;
  EXECUTE format('CREATE ROLE omega_cartridge_sap_b1 LOGIN PASSWORD %L', pw);
END $$;

DO $$
DECLARE
  tbl TEXT;
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'omega_cartridge_sap_b1') THEN
    RETURN;
  END IF;
  GRANT CONNECT ON DATABASE modecissions TO omega_cartridge_sap_b1;
  GRANT USAGE ON SCHEMA public TO omega_cartridge_sap_b1;
  GRANT CREATE ON SCHEMA public TO omega_cartridge_sap_b1;
  FOREACH tbl IN ARRAY ARRAY[
    'cartridges', 'entity_config', 'kb_config',
    'entity_watermarks', 'extraction_runs', 'kb_runs', 'jobs', 'run_logs'
  ] LOOP
    IF to_regclass(format('public.%I', tbl)) IS NOT NULL THEN
      EXECUTE format('GRANT SELECT, INSERT, UPDATE ON public.%I TO omega_cartridge_sap_b1', tbl);
    END IF;
  END LOOP;
  FOREACH tbl IN ARRAY ARRAY['mcp_servers', 'mcp_custom_tools'] LOOP
    IF to_regclass(format('public.%I', tbl)) IS NOT NULL THEN
      EXECUTE format('GRANT SELECT ON public.%I TO omega_cartridge_sap_b1', tbl);
    END IF;
  END LOOP;
  GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO omega_cartridge_sap_b1;
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'omega_cartridge_jobs_owner') THEN
    GRANT omega_cartridge_jobs_owner TO omega_cartridge_sap_b1;
  END IF;
END $$;

DO $$
DECLARE
  tbl TEXT;
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'omega_cartridge_sap_b1') THEN
    RETURN;
  END IF;
  FOREACH tbl IN ARRAY ARRAY[
    'users', 'user_sessions', 'user_tokens', 'refresh_tokens',
    'tenants', 'workspaces', 'roles', 'user_workspace_roles',
    'decisions', 'decision_actions',
    'audit_events', 'login_attempts', 'vault_access_log', 'vault_entries',
    'conversations', 'conversation_messages'
  ] LOOP
    IF to_regclass(format('public.%I', tbl)) IS NOT NULL THEN
      EXECUTE format('REVOKE ALL PRIVILEGES ON public.%I FROM omega_cartridge_sap_b1', tbl);
    END IF;
  END LOOP;
END $$;

DO $$
DECLARE
  tbl TEXT;
  tenant_expr TEXT;
  scope_expr TEXT;
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'omega_cartridge_sap_b1') THEN
    RETURN;
  END IF;

  IF to_regclass('public.entity_watermarks') IS NOT NULL THEN
    DROP POLICY IF EXISTS entity_watermarks_workspace_rls_sap_b1 ON public.entity_watermarks;
    CREATE POLICY entity_watermarks_workspace_rls_sap_b1 ON public.entity_watermarks
      FOR ALL TO omega_cartridge_sap_b1
      USING (
        current_setting('app.platform_admin', true) = 'true'
        OR omega_rls_workspace_matches(tenant_id, workspace_id)
      )
      WITH CHECK (
        current_setting('app.platform_admin', true) = 'true'
        OR omega_rls_workspace_matches(tenant_id, workspace_id)
      );
  END IF;

  FOREACH tbl IN ARRAY ARRAY['run_logs', 'extraction_runs', 'jobs', 'kb_runs'] LOOP
    IF to_regclass(format('public.%I', tbl)) IS NULL THEN
      CONTINUE;
    END IF;
    IF NOT EXISTS (
      SELECT 1 FROM information_schema.columns
       WHERE table_schema = 'public' AND table_name = tbl AND column_name = 'scope_status'
    ) THEN
      CONTINUE;
    END IF;
    EXECUTE format('DROP POLICY IF EXISTS %I ON public.%I', tbl || '_20c_scoped_rls_sap_b1', tbl);
    EXECUTE format(
      'CREATE POLICY %I ON public.%I
         FOR ALL
         TO omega_cartridge_sap_b1
         USING (scope_status = ''scoped'' AND omega_rls_workspace_matches(tenant_id, workspace_id))
         WITH CHECK (scope_status = ''scoped'' AND omega_rls_workspace_matches(tenant_id, workspace_id))',
      tbl || '_20c_scoped_rls_sap_b1', tbl
    );
    EXECUTE format('DROP POLICY IF EXISTS %I ON public.%I', tbl || '_20c_legacy_platform_audit_rls_sap_b1', tbl);
    EXECUTE format(
      'CREATE POLICY %I ON public.%I
         FOR SELECT
         TO omega_cartridge_sap_b1
         USING (scope_status = ''legacy_unscoped'' AND omega_20b_platform_audit_context())',
      tbl || '_20c_legacy_platform_audit_rls_sap_b1', tbl
    );
  END LOOP;

  FOREACH tbl IN ARRAY ARRAY[
    'cartridges', 'entity_config', 'kb_config',
    'entity_watermarks', 'extraction_runs', 'kb_runs', 'jobs', 'run_logs'
  ] LOOP
    IF to_regclass(format('public.%I', tbl)) IS NULL THEN
      CONTINUE;
    END IF;
    IF NOT EXISTS (
      SELECT 1 FROM pg_policies
       WHERE schemaname = 'public' AND tablename = tbl
         AND policyname = tbl || '_tenant_workspace_rls'
    ) THEN
      CONTINUE;
    END IF;
    IF EXISTS (
      SELECT 1 FROM information_schema.columns
       WHERE table_schema = 'public' AND table_name = tbl AND column_name = 'tenant_id'
    ) THEN
      tenant_expr := 'tenant_id';
    ELSE
      tenant_expr := 'NULL::uuid';
    END IF;
    scope_expr := format('omega_rls_workspace_matches(%s, workspace_id)', tenant_expr);
    EXECUTE format('DROP POLICY IF EXISTS %I ON public.%I', tbl || '_tenant_workspace_rls_sap_b1', tbl);
    EXECUTE format(
      'CREATE POLICY %I ON public.%I FOR ALL TO omega_cartridge_sap_b1 USING (%s) WITH CHECK (%s)',
      tbl || '_tenant_workspace_rls_sap_b1', tbl, scope_expr, scope_expr
    );
  END LOOP;
END $$;

INSERT INTO schema_migrations (filename, applied_at)
VALUES ('99zzzzl_sap_b1_cartridge_role.sql', NOW())
ON CONFLICT (filename) DO NOTHING;
