-- Native RLS for postgres_gold.
--
-- The Refinement AST guard remains the first line of defense for pggold reads.
-- This migration adds a database-level backstop so gold_* tables are also
-- workspace-scoped if any direct Postgres access is introduced later.

ALTER ROLE omega_refinement_gold NOBYPASSRLS;

CREATE OR REPLACE FUNCTION public.omega_gold_workspace_matches(
    row_tenant text,
    row_workspace text
) RETURNS boolean
LANGUAGE sql
STABLE
AS $$
    SELECT
        NULLIF(current_setting('app.tenant_id', true), '') IS NOT NULL
        AND NULLIF(current_setting('app.workspace_id', true), '') IS NOT NULL
        AND row_tenant = NULLIF(current_setting('app.tenant_id', true), '')
        AND row_workspace = NULLIF(current_setting('app.workspace_id', true), '')
$$;

CREATE OR REPLACE FUNCTION public.omega_apply_gold_rls_for_table(p_table_name text)
RETURNS void
LANGUAGE plpgsql
AS $$
DECLARE
    has_tenant boolean;
    has_workspace boolean;
    policy_name text;
BEGIN
    IF p_table_name IS NULL OR p_table_name !~ '^gold_[A-Za-z0-9_]+$' THEN
        RAISE EXCEPTION 'invalid gold table name: %', p_table_name;
    END IF;

    IF to_regclass(format('public.%I', p_table_name)) IS NULL THEN
        RAISE EXCEPTION 'gold table does not exist: %', p_table_name;
    END IF;

    SELECT EXISTS (
        SELECT 1
          FROM information_schema.columns AS c
         WHERE c.table_schema = 'public'
           AND c.table_name = p_table_name
           AND c.column_name = 'tenant_id'
    ) INTO has_tenant;

    SELECT EXISTS (
        SELECT 1
          FROM information_schema.columns AS c
         WHERE c.table_schema = 'public'
           AND c.table_name = p_table_name
           AND c.column_name = 'workspace_id'
    ) INTO has_workspace;

    policy_name := p_table_name || '_tenant_workspace_rls';

    EXECUTE format('ALTER TABLE public.%I ENABLE ROW LEVEL SECURITY', p_table_name);
    EXECUTE format('ALTER TABLE public.%I FORCE ROW LEVEL SECURITY', p_table_name);
    EXECUTE format('DROP POLICY IF EXISTS %I ON public.%I', policy_name, p_table_name);

    IF has_tenant AND has_workspace THEN
        EXECUTE format(
            'CREATE POLICY %I ON public.%I
                USING (public.omega_gold_workspace_matches(tenant_id::text, workspace_id::text))
                WITH CHECK (public.omega_gold_workspace_matches(tenant_id::text, workspace_id::text))',
            policy_name,
            p_table_name
        );
    ELSE
        -- Legacy/unscoped Gold tables are default-deny until they are
        -- recreated through the scoped materialization path.
        EXECUTE format(
            'CREATE POLICY %I ON public.%I USING (false) WITH CHECK (false)',
            policy_name,
            p_table_name
        );
    END IF;
END $$;

DO $$
DECLARE
    tbl record;
BEGIN
    FOR tbl IN
        SELECT tablename
          FROM pg_tables
         WHERE schemaname = 'public'
           AND tablename LIKE 'gold\_%' ESCAPE '\'
    LOOP
        PERFORM public.omega_apply_gold_rls_for_table(tbl.tablename);
    END LOOP;
END $$;

GRANT EXECUTE ON FUNCTION public.omega_gold_workspace_matches(text, text) TO omega_refinement_gold;
GRANT EXECUTE ON FUNCTION public.omega_apply_gold_rls_for_table(text) TO omega_refinement_gold;
