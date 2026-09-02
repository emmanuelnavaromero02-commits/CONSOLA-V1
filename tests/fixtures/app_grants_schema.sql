-- Minimal slice of the real schema the app-grant tests need.
--
-- Mirrors infra/init: tenants/workspaces (13_rbac_models), datasets
-- (00_schema + 23 + 99zd), analytic_apps (08/10), cartridge_installations
-- (73_marketplace_installations) and the RLS helper (99e). Kept small on
-- purpose so CI can stand it up in seconds; the objects under test come from
-- the real 99zzt and 99zzu, applied on top of this.

CREATE EXTENSION IF NOT EXISTS pgcrypto;
CREATE TABLE schema_migrations (
  filename TEXT PRIMARY KEY,
  applied_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
-- Roles as created by the real stack.
DO $$
DECLARE r TEXT;
BEGIN
  FOREACH r IN ARRAY ARRAY['omega_console','omega_refinement','omega_workspace','omega_mcp_infra','omega_airflow_dag'] LOOP
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = r) THEN
      EXECUTE format('CREATE ROLE %I LOGIN PASSWORD %L', r, 'test');
    END IF;
  END LOOP;
END $$;
CREATE TABLE users (id BIGSERIAL PRIMARY KEY, is_active BOOLEAN NOT NULL DEFAULT TRUE, tenant_id UUID);
CREATE TABLE roles (id SERIAL PRIMARY KEY, name TEXT UNIQUE NOT NULL);
CREATE TABLE tenants (id UUID PRIMARY KEY DEFAULT gen_random_uuid(), name TEXT UNIQUE NOT NULL, created_at TIMESTAMPTZ NOT NULL DEFAULT NOW());
CREATE TABLE workspaces (id UUID PRIMARY KEY DEFAULT gen_random_uuid(), tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE, name TEXT NOT NULL, created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(), CONSTRAINT uq_workspaces_tenant_name UNIQUE (tenant_id, name));
CREATE TABLE datasets (name TEXT NOT NULL, description TEXT NOT NULL DEFAULT '', layer TEXT NOT NULL, cartridge TEXT NOT NULL DEFAULT '', workspace_id UUID, tenant_id UUID REFERENCES tenants(id) ON DELETE SET NULL, scope_status TEXT NOT NULL DEFAULT 'scoped', created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(), CONSTRAINT datasets_workspace_name_key UNIQUE (workspace_id, name));
CREATE TABLE analytic_apps (name TEXT PRIMARY KEY, title TEXT NOT NULL, html TEXT NOT NULL, description TEXT, cartridge_id TEXT, created_by_id BIGINT REFERENCES users(id) ON DELETE SET NULL, visibility TEXT NOT NULL DEFAULT 'private', datasets_used TEXT[], tenant_id UUID REFERENCES tenants(id) ON DELETE SET NULL, workspace_id UUID REFERENCES workspaces(id) ON DELETE SET NULL, scope_status TEXT NOT NULL DEFAULT 'platform_template');
CREATE TABLE cartridges (id TEXT PRIMARY KEY);
CREATE TABLE marketplace_products (id TEXT PRIMARY KEY);
CREATE TABLE cartridge_installations (id TEXT PRIMARY KEY, tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE, workspace_id UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE, cartridge_id TEXT NOT NULL REFERENCES cartridges(id) ON DELETE CASCADE, status TEXT NOT NULL DEFAULT 'ready');
CREATE UNIQUE INDEX cartridge_installations_tenant_workspace_cartridge_uniq ON cartridge_installations(tenant_id, workspace_id, cartridge_id);
CREATE OR REPLACE FUNCTION omega_rls_workspace_matches(row_tenant uuid, row_workspace uuid) RETURNS boolean LANGUAGE sql STABLE AS $$
  SELECT row_workspace IS NOT NULL
     AND NULLIF(current_setting('app.workspace_id', true), '') IS NOT NULL
     AND row_workspace::text = NULLIF(current_setting('app.workspace_id', true), '')
     AND (row_tenant IS NULL OR (NULLIF(current_setting('app.tenant_id', true), '') IS NOT NULL AND row_tenant::text = NULLIF(current_setting('app.tenant_id', true), '')));
$$;
GRANT USAGE ON SCHEMA public TO omega_console, omega_refinement, omega_workspace, omega_mcp_infra, omega_airflow_dag;
GRANT SELECT ON ALL TABLES IN SCHEMA public TO omega_console, omega_refinement, omega_workspace;

-- Mirror the FORCE RLS boundary from the production migrations.  The grant
-- reconciler's owner must receive its own scoped read policies from the repair
-- migration under test; ACL grants alone are intentionally insufficient.
ALTER TABLE datasets ENABLE ROW LEVEL SECURITY;
ALTER TABLE datasets FORCE ROW LEVEL SECURITY;
CREATE POLICY datasets_fixture_scope ON datasets
  FOR ALL TO omega_console, omega_refinement, omega_workspace
  USING (omega_rls_workspace_matches(tenant_id, workspace_id))
  WITH CHECK (omega_rls_workspace_matches(tenant_id, workspace_id));

ALTER TABLE cartridge_installations ENABLE ROW LEVEL SECURITY;
ALTER TABLE cartridge_installations FORCE ROW LEVEL SECURITY;
CREATE POLICY cartridge_installations_fixture_scope ON cartridge_installations
  FOR ALL TO omega_console, omega_refinement, omega_workspace
  USING (omega_rls_workspace_matches(tenant_id, workspace_id))
  WITH CHECK (omega_rls_workspace_matches(tenant_id, workspace_id));

ALTER TABLE analytic_apps ENABLE ROW LEVEL SECURITY;
ALTER TABLE analytic_apps FORCE ROW LEVEL SECURITY;
CREATE POLICY analytic_apps_fixture_scoped ON analytic_apps
  FOR ALL TO omega_console, omega_refinement
  USING (
    scope_status = 'scoped'
    AND omega_rls_workspace_matches(tenant_id, workspace_id)
  )
  WITH CHECK (
    scope_status = 'scoped'
    AND omega_rls_workspace_matches(tenant_id, workspace_id)
  );
CREATE POLICY analytic_apps_fixture_template_read ON analytic_apps
  FOR SELECT TO omega_console, omega_refinement
  USING (
    scope_status = 'platform_template'
    AND tenant_id IS NULL
    AND workspace_id IS NULL
    AND created_by_id IS NULL
  );
