-- P0-RLS-002: database-level tenant-isolation backstop for gold_*/master_* tables.
--
-- The application layer already (a) filters gold reads by tenant_id/workspace_id
-- (refinement._inject_rls_ast) and (b) forces the write scope server-side
-- (refinement._ensure_scope_columns, P0-RLS-001). This migration adds a Postgres
-- ROW LEVEL SECURITY backstop so that a future app-layer bug or a direct query
-- path cannot read or write across tenants.
--
-- SAFE BY DEFAULT — this migration changes NO behaviour on its own:
--   * The policy is PERMISSIVE unless the session sets `omega.rls_enforce = 'on'`.
--     With the flag unset, current_setting(...,true) is NULL and the USING/CHECK
--     short-circuit to TRUE, so every existing read/write keeps working.
--   * Merging it cannot brick gold reads/writes; it only installs the machinery
--     and applies the (dormant) policy to gold_*/master_* tables.
--
-- ACTIVATION is a deliberate, validated follow-up (see
-- docs/runbook/11_activar_gold_rls.md):
--   1. Wire refinement to SET omega.tenant_id / omega.workspace_id per request on
--      the pggold connection — validated against DuckDB's connection reuse on a
--      LIVE stack (the DuckDB postgres ATTACH is long-lived and shared, so the
--      GUC must be set/reset per request, not per connection).
--   2. Only then set omega.rls_enforce = 'on'.
-- Verified on PostgreSQL 16: permissive-by-default, tenant isolation on enforce,
-- and WITH CHECK rejection of forged cross-tenant inserts.

-- ── Helper: enable RLS + install the tenant-isolation policy on one table ──────
CREATE OR REPLACE FUNCTION public.omega_apply_gold_rls(target regclass)
RETURNS void
LANGUAGE plpgsql
AS $fn$
DECLARE
  has_tenant boolean;
  has_ws     boolean;
  polname    text := 'omega_tenant_isolation';
BEGIN
  SELECT
    bool_or(attname = 'tenant_id'),
    bool_or(attname = 'workspace_id')
  INTO has_tenant, has_ws
  FROM pg_attribute
  WHERE attrelid = target AND attnum > 0 AND NOT attisdropped;

  -- Only scope-bearing tables are isolatable.
  IF NOT (coalesce(has_tenant, false) AND coalesce(has_ws, false)) THEN
    RETURN;
  END IF;

  EXECUTE format('ALTER TABLE %s ENABLE ROW LEVEL SECURITY', target);
  -- FORCE so the policy also applies to the table owner (refinement), which is
  -- the role that creates and writes gold_* tables.
  EXECUTE format('ALTER TABLE %s FORCE ROW LEVEL SECURITY', target);
  EXECUTE format('DROP POLICY IF EXISTS %I ON %s', polname, target);
  EXECUTE format($p$
    CREATE POLICY %I ON %s
      USING (
        coalesce(current_setting('omega.rls_enforce', true), 'off') <> 'on'
        OR (
          tenant_id::text    = current_setting('omega.tenant_id', true)
          AND workspace_id::text = current_setting('omega.workspace_id', true)
        )
      )
      WITH CHECK (
        coalesce(current_setting('omega.rls_enforce', true), 'off') <> 'on'
        OR (
          tenant_id::text    = current_setting('omega.tenant_id', true)
          AND workspace_id::text = current_setting('omega.workspace_id', true)
        )
      )
  $p$, polname, target);
END;
$fn$;

-- ── Apply to any gold_*/master_* tables that already exist ────────────────────
DO $$
DECLARE r record;
BEGIN
  FOR r IN
    SELECT c.oid::regclass AS rel
    FROM pg_class c
    JOIN pg_namespace n ON n.oid = c.relnamespace
    WHERE n.nspname = 'public'
      AND c.relkind = 'r'
      AND (c.relname LIKE 'gold\_%' ESCAPE '\' OR c.relname LIKE 'master\_%' ESCAPE '\')
  LOOP
    PERFORM public.omega_apply_gold_rls(r.rel);
  END LOOP;
END $$;

-- ── Auto-apply to FUTURE gold_*/master_* tables via an event trigger ──────────
CREATE OR REPLACE FUNCTION public.omega_gold_rls_event()
RETURNS event_trigger
LANGUAGE plpgsql
AS $fn$
DECLARE obj record;
BEGIN
  FOR obj IN SELECT * FROM pg_event_trigger_ddl_commands() LOOP
    IF obj.object_type = 'table'
       AND obj.schema_name = 'public'
       AND (obj.object_identity LIKE 'public.gold\_%' ESCAPE '\'
            OR obj.object_identity LIKE 'public.master\_%' ESCAPE '\')
    THEN
      PERFORM public.omega_apply_gold_rls(obj.objid::regclass);
    END IF;
  END LOOP;
END;
$fn$;

DROP EVENT TRIGGER IF EXISTS omega_gold_rls_on_create;
CREATE EVENT TRIGGER omega_gold_rls_on_create
  ON ddl_command_end
  WHEN TAG IN ('CREATE TABLE', 'CREATE TABLE AS', 'SELECT INTO')
  EXECUTE FUNCTION public.omega_gold_rls_event();
