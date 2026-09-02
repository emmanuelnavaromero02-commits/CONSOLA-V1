-- 99zzy_analytic_app_grants_owner_rls_repair.sql
--
-- The grant reconciler is SECURITY DEFINER and owned by the deliberately
-- constrained omega_app_grants_owner role.  ACL SELECT grants alone are not
-- enough because every source table below uses FORCE ROW LEVEL SECURITY.
-- Give that role read-only access to the caller's transaction-local scope so
-- reconciliation can validate the installation, app ownership and datasets.

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_roles WHERE rolname = 'omega_app_grants_owner'
    ) THEN
        RAISE EXCEPTION
            'omega_app_grants_owner must exist before applying app-grant RLS repair';
    END IF;

    IF to_regclass('public.cartridge_installations') IS NOT NULL THEN
        DROP POLICY IF EXISTS cartridge_installations_app_grants_owner_read
            ON public.cartridge_installations;
        CREATE POLICY cartridge_installations_app_grants_owner_read
            ON public.cartridge_installations
            FOR SELECT TO omega_app_grants_owner
            USING (public.omega_rls_workspace_matches(tenant_id, workspace_id));
    END IF;

    IF to_regclass('public.analytic_apps') IS NOT NULL THEN
        DROP POLICY IF EXISTS analytic_apps_app_grants_owner_read
            ON public.analytic_apps;
        CREATE POLICY analytic_apps_app_grants_owner_read
            ON public.analytic_apps
            FOR SELECT TO omega_app_grants_owner
            USING (
                EXISTS (
                    SELECT 1
                      FROM public.analytic_app_manifests m
                     WHERE m.app_name = analytic_apps.name
                       AND m.revision = 'active'
                       AND m.source = 'packaged_manifest'
                )
            );
    END IF;

    IF to_regclass('public.datasets') IS NOT NULL THEN
        DROP POLICY IF EXISTS datasets_app_grants_owner_read
            ON public.datasets;
        CREATE POLICY datasets_app_grants_owner_read
            ON public.datasets
            FOR SELECT TO omega_app_grants_owner
            USING (
                scope_status = 'scoped'
                AND public.omega_rls_workspace_matches(tenant_id, workspace_id)
            );
    END IF;
END $$;

-- Replace the original reconciler with a fail-closed packaged-app identity
-- check.  SELECT ... INTO a nullable owner alone cannot distinguish a missing
-- row from a legitimate packaged row whose created_by_id is NULL.  The policy
-- above deliberately exposes every analytic_apps row whose name collides with
-- an active packaged manifest, regardless of its scope, so this function can
-- reject the collision instead of mistaking RLS invisibility for ownership.
CREATE OR REPLACE FUNCTION public.reconcile_analytic_app_dataset_grants(
    p_app_name TEXT,
    p_expected_digest TEXT DEFAULT NULL
) RETURNS TABLE (dataset_name TEXT, action TEXT)
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
AS $reconcile_hardened$
DECLARE
    scoped_tenant UUID;
    scoped_workspace UUID;
    resolved_cartridge TEXT;
    resolved_digest TEXT;
    app_owner BIGINT;
    app_cartridge TEXT;
    app_scope_status TEXT;
    app_tenant UUID;
    app_workspace UUID;
    target TEXT;
    new_id BIGINT;
    stale RECORD;
    actor CONSTANT TEXT := 'server:packaged_manifest';
BEGIN
    scoped_tenant := NULLIF(current_setting('app.tenant_id', TRUE), '')::UUID;
    scoped_workspace := NULLIF(current_setting('app.workspace_id', TRUE), '')::UUID;
    IF scoped_tenant IS NULL OR scoped_workspace IS NULL THEN
        RAISE EXCEPTION 'app grant scope is unavailable' USING ERRCODE = '42501';
    END IF;
    IF p_app_name IS NULL OR p_app_name !~ '^[a-zA-Z_][a-zA-Z0-9_]*$' THEN
        RAISE EXCEPTION 'app name is invalid' USING ERRCODE = '22023';
    END IF;

    SELECT m.cartridge_id, m.manifest_digest
      INTO resolved_cartridge, resolved_digest
      FROM public.analytic_app_manifests m
     WHERE m.app_name = p_app_name
       AND m.revision = 'active'
       AND m.source = 'packaged_manifest';
    IF resolved_cartridge IS NULL THEN
        RETURN;
    END IF;
    IF p_expected_digest IS NOT NULL
       AND p_expected_digest IS DISTINCT FROM resolved_digest THEN
        RAISE EXCEPTION 'app manifest digest is stale' USING ERRCODE = '22023';
    END IF;

    SELECT a.created_by_id,
           a.cartridge_id,
           a.scope_status,
           a.tenant_id,
           a.workspace_id
      INTO app_owner,
           app_cartridge,
           app_scope_status,
           app_tenant,
           app_workspace
      FROM public.analytic_apps a
     WHERE a.name = p_app_name;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'packaged app is not registered' USING ERRCODE = '42501';
    END IF;
    IF app_owner IS NOT NULL
       OR app_cartridge IS DISTINCT FROM resolved_cartridge
       OR app_scope_status IS DISTINCT FROM 'platform_template'
       OR app_tenant IS NOT NULL
       OR app_workspace IS NOT NULL THEN
        RAISE EXCEPTION 'app is not packaged' USING ERRCODE = '42501';
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM public.cartridge_installations ci
         WHERE ci.tenant_id = scoped_tenant
           AND ci.workspace_id = scoped_workspace
           AND ci.cartridge_id = resolved_cartridge
           AND ci.status = 'ready'
    ) THEN
        RAISE EXCEPTION 'cartridge is not installed for this workspace'
            USING ERRCODE = '42501';
    END IF;

    FOR stale IN
        SELECT g.id, g.dataset_name
          FROM public.analytic_app_dataset_grants g
         WHERE g.tenant_id = scoped_tenant
           AND g.workspace_id = scoped_workspace
           AND g.app_name = p_app_name
           AND g.revoked_at IS NULL
           AND (g.manifest_digest <> resolved_digest
                OR NOT EXISTS (
                    SELECT 1 FROM public.analytic_app_manifest_datasets d
                     WHERE d.app_name = p_app_name
                       AND d.manifest_digest = resolved_digest
                       AND d.dataset_name = g.dataset_name))
    LOOP
        UPDATE public.analytic_app_dataset_grants
           SET revoked_at = clock_timestamp(),
               revoked_by = actor,
               revoke_reason = 'manifest_reconciliation'
         WHERE id = stale.id;
        INSERT INTO public.analytic_app_dataset_grant_events
            (grant_id, tenant_id, workspace_id, event, actor, detail)
        VALUES (stale.id, scoped_tenant, scoped_workspace, 'revoked',
                actor, 'superseded by ' || resolved_digest);
        dataset_name := stale.dataset_name;
        action := 'revoked';
        RETURN NEXT;
    END LOOP;

    FOR target IN
        SELECT d.dataset_name
          FROM public.analytic_app_manifest_datasets d
         WHERE d.app_name = p_app_name
           AND d.manifest_digest = resolved_digest
         ORDER BY d.dataset_name
    LOOP
        CONTINUE WHEN NOT EXISTS (
            SELECT 1 FROM public.datasets ds
             WHERE ds.tenant_id = scoped_tenant
               AND ds.workspace_id = scoped_workspace
               AND ds.name = target
               AND ds.cartridge = resolved_cartridge
               AND ds.scope_status = 'scoped'
        );
        CONTINUE WHEN EXISTS (
            SELECT 1 FROM public.analytic_app_dataset_grants g
             WHERE g.tenant_id = scoped_tenant
               AND g.workspace_id = scoped_workspace
               AND g.app_name = p_app_name
               AND g.dataset_name = target
               AND g.manifest_digest = resolved_digest
               AND g.revoked_at IS NULL
        );
        INSERT INTO public.analytic_app_dataset_grants
            (tenant_id, workspace_id, app_name, cartridge_id, dataset_name,
             manifest_digest, grant_source, granted_by)
        VALUES (scoped_tenant, scoped_workspace, p_app_name, resolved_cartridge,
                target, resolved_digest, 'packaged_manifest', actor)
        RETURNING id INTO new_id;
        INSERT INTO public.analytic_app_dataset_grant_events
            (grant_id, tenant_id, workspace_id, event, actor, detail)
        VALUES (new_id, scoped_tenant, scoped_workspace, 'granted',
                actor, resolved_digest);
        dataset_name := target;
        action := 'granted';
        RETURN NEXT;
    END LOOP;
END
$reconcile_hardened$;

ALTER FUNCTION public.reconcile_analytic_app_dataset_grants(TEXT, TEXT)
    OWNER TO omega_app_grants_owner;
REVOKE ALL ON FUNCTION public.reconcile_analytic_app_dataset_grants(TEXT, TEXT)
    FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.reconcile_analytic_app_dataset_grants(TEXT, TEXT)
    TO omega_console;

INSERT INTO schema_migrations (filename, applied_at)
VALUES ('99zzy_analytic_app_grants_owner_rls_repair.sql', NOW())
ON CONFLICT (filename) DO NOTHING;
