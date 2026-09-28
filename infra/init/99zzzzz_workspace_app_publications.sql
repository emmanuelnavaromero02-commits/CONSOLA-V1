-- Workspace-published analytic apps: manifest registry rows, grant ledger
-- widening and the SECURITY DEFINER registration/reconciliation pair.
-- Security idioms copied from 99zzt/99zzy: schema-qualified names,
-- search_path = pg_catalog, pg_temp, owner omega_app_grants_owner.

CREATE OR REPLACE FUNCTION omega_rls_workspace_text_matches(row_tenant text, row_workspace text)
RETURNS boolean
LANGUAGE sql
STABLE
AS $$
    SELECT row_tenant IS NOT NULL
       AND row_workspace IS NOT NULL
       AND NULLIF(current_setting('app.tenant_id', true), '') IS NOT NULL
       AND NULLIF(current_setting('app.workspace_id', true), '') IS NOT NULL
       AND row_tenant = NULLIF(current_setting('app.tenant_id', true), '')
       AND row_workspace = NULLIF(current_setting('app.workspace_id', true), '')
$$;


-- ── manifest registry: workspace publication columns ───────────────────────
ALTER TABLE public.analytic_app_manifests
    ADD COLUMN IF NOT EXISTS tenant_id TEXT,
    ADD COLUMN IF NOT EXISTS workspace_id TEXT,
    ADD COLUMN IF NOT EXISTS created_by_user_id TEXT;

ALTER TABLE public.analytic_app_manifests
    DROP CONSTRAINT IF EXISTS analytic_app_manifests_source_check;
ALTER TABLE public.analytic_app_manifests
    ADD CONSTRAINT analytic_app_manifests_source_check
    CHECK (source IN ('packaged_manifest', 'workspace_publication'));

-- Packaged rows stay scope-free; workspace rows must carry the full scope.
ALTER TABLE public.analytic_app_manifests
    DROP CONSTRAINT IF EXISTS analytic_app_manifests_scope_check;
ALTER TABLE public.analytic_app_manifests
    ADD CONSTRAINT analytic_app_manifests_scope_check
    CHECK (
        (source = 'packaged_manifest'
         AND tenant_id IS NULL AND workspace_id IS NULL
         AND created_by_user_id IS NULL)
        OR
        (source = 'workspace_publication'
         AND tenant_id IS NOT NULL AND workspace_id IS NOT NULL
         AND created_by_user_id IS NOT NULL)
    );

ALTER TABLE public.analytic_app_manifests ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.analytic_app_manifests FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS analytic_app_manifests_scope_read
    ON public.analytic_app_manifests;
CREATE POLICY analytic_app_manifests_scope_read
    ON public.analytic_app_manifests
    FOR SELECT TO omega_console, omega_refinement, omega_workspace,
                  omega_app_grants_owner
    USING (source = 'packaged_manifest'
           OR public.omega_rls_workspace_text_matches(tenant_id, workspace_id));
-- Writes only through the definer functions, and only for workspace rows.
DROP POLICY IF EXISTS analytic_app_manifests_owner_write
    ON public.analytic_app_manifests;
CREATE POLICY analytic_app_manifests_owner_write
    ON public.analytic_app_manifests
    FOR ALL TO omega_app_grants_owner
    USING (source = 'workspace_publication'
           AND public.omega_rls_workspace_text_matches(tenant_id, workspace_id))
    WITH CHECK (source = 'workspace_publication'
                AND public.omega_rls_workspace_text_matches(tenant_id, workspace_id));

GRANT INSERT, UPDATE ON public.analytic_app_manifests TO omega_app_grants_owner;
GRANT INSERT, DELETE ON public.analytic_app_manifest_datasets
    TO omega_app_grants_owner;

-- The registration function must see the caller's scoped workspace apps, not
-- only packaged-name collisions.
DROP POLICY IF EXISTS analytic_apps_app_grants_owner_read ON public.analytic_apps;
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
        OR public.omega_rls_workspace_matches(tenant_id, workspace_id)
    );


-- ── grants ledger: admit the workspace publication source ──────────────────
ALTER TABLE public.analytic_app_dataset_grants
    DROP CONSTRAINT IF EXISTS analytic_app_dataset_grants_source_check;
ALTER TABLE public.analytic_app_dataset_grants
    ADD CONSTRAINT analytic_app_dataset_grants_source_check
    CHECK (grant_source IN ('packaged_manifest', 'workspace_publication'));

ALTER TABLE public.analytic_app_dataset_grants
    DROP CONSTRAINT IF EXISTS analytic_app_dataset_grants_actor_check;
ALTER TABLE public.analytic_app_dataset_grants
    ADD CONSTRAINT analytic_app_dataset_grants_actor_check
    CHECK (granted_by IN ('server:packaged_manifest',
                          'server:workspace_publication'));


-- ── workspace publication registration ─────────────────────────────────────
-- The digest is computed server-side in console Python with the packaged
-- manifest_digest() (cartridge sentinel 'workspace') and stored verbatim.
CREATE OR REPLACE FUNCTION public.register_workspace_app_manifest(
    p_app_name TEXT,
    p_html_sha256 TEXT,
    p_datasets TEXT[],
    p_manifest_digest TEXT
) RETURNS TEXT
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
AS $register$
DECLARE
    scoped_tenant UUID;
    scoped_workspace UUID;
    app_owner BIGINT;
    app_scope_status TEXT;
    app_tenant UUID;
    app_workspace UUID;
    app_html TEXT;
    target TEXT;
BEGIN
    scoped_tenant := NULLIF(current_setting('app.tenant_id', TRUE), '')::UUID;
    scoped_workspace := NULLIF(current_setting('app.workspace_id', TRUE), '')::UUID;
    IF scoped_tenant IS NULL OR scoped_workspace IS NULL THEN
        RAISE EXCEPTION 'app publication scope is unavailable' USING ERRCODE = '42501';
    END IF;
    IF p_app_name IS NULL OR p_app_name !~ '^[a-zA-Z_][a-zA-Z0-9_]*$' THEN
        RAISE EXCEPTION 'app name is invalid' USING ERRCODE = '22023';
    END IF;
    IF p_html_sha256 IS NULL OR p_html_sha256 !~ '^[0-9a-f]{64}$' THEN
        RAISE EXCEPTION 'html digest is invalid' USING ERRCODE = '22023';
    END IF;
    IF p_manifest_digest IS NULL OR p_manifest_digest !~ '^[0-9a-f]{64}$' THEN
        RAISE EXCEPTION 'manifest digest is invalid' USING ERRCODE = '22023';
    END IF;
    IF p_datasets IS NULL THEN
        RAISE EXCEPTION 'dataset list is required' USING ERRCODE = '22023';
    END IF;
    FOREACH target IN ARRAY p_datasets LOOP
        IF target IS NULL OR target !~ '^[a-zA-Z_][a-zA-Z0-9_]*$' THEN
            RAISE EXCEPTION 'dataset name is invalid' USING ERRCODE = '22023';
        END IF;
        IF NOT EXISTS (
            SELECT 1 FROM public.datasets ds
             WHERE ds.tenant_id = scoped_tenant
               AND ds.workspace_id = scoped_workspace
               AND ds.name = target
               AND ds.scope_status = 'scoped'
        ) THEN
            RAISE EXCEPTION 'dataset is not available in this workspace'
                USING ERRCODE = '42501';
        END IF;
    END LOOP;

    -- A workspace publication may never shadow a packaged/system app.
    IF EXISTS (
        SELECT 1 FROM public.analytic_app_manifests m
         WHERE m.app_name = p_app_name
           AND m.source = 'packaged_manifest'
    ) THEN
        RAISE EXCEPTION 'app name belongs to a packaged app' USING ERRCODE = '42501';
    END IF;

    SELECT a.created_by_id, a.scope_status, a.tenant_id, a.workspace_id, a.html
      INTO app_owner, app_scope_status, app_tenant, app_workspace, app_html
      FROM public.analytic_apps a
     WHERE a.name = p_app_name;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'workspace app is not registered' USING ERRCODE = '42501';
    END IF;
    IF app_owner IS NULL
       OR app_scope_status IS DISTINCT FROM 'scoped'
       OR app_tenant IS DISTINCT FROM scoped_tenant
       OR app_workspace IS DISTINCT FROM scoped_workspace THEN
        RAISE EXCEPTION 'app is not a workspace publication for this scope'
            USING ERRCODE = '42501';
    END IF;
    -- The stored HTML is the served truth; the registered hash must match it.
    IF encode(sha256(convert_to(app_html, 'UTF8')), 'hex') <> p_html_sha256 THEN
        RAISE EXCEPTION 'published html does not match the registered digest'
            USING ERRCODE = '22023';
    END IF;

    UPDATE public.analytic_app_manifests
       SET cartridge_id = 'workspace',
           manifest_digest = p_manifest_digest,
           html_sha256 = p_html_sha256,
           revision = 'active',
           source = 'workspace_publication',
           generated_at = clock_timestamp(),
           tenant_id = scoped_tenant::text,
           workspace_id = scoped_workspace::text,
           created_by_user_id = app_owner::text
     WHERE app_name = p_app_name
       AND source = 'workspace_publication';
    IF NOT FOUND THEN
        INSERT INTO public.analytic_app_manifests
            (app_name, cartridge_id, manifest_digest, html_sha256, revision,
             source, tenant_id, workspace_id, created_by_user_id)
        VALUES (p_app_name, 'workspace', p_manifest_digest, p_html_sha256,
                'active', 'workspace_publication', scoped_tenant::text,
                scoped_workspace::text, app_owner::text);
    END IF;

    -- Superseded revisions keep only the dataset rows grant history references.
    DELETE FROM public.analytic_app_manifest_datasets d
     WHERE d.app_name = p_app_name
       AND d.manifest_digest <> p_manifest_digest
       AND NOT EXISTS (
           SELECT 1 FROM public.analytic_app_dataset_grants g
            WHERE g.app_name = d.app_name
              AND g.manifest_digest = d.manifest_digest
              AND g.dataset_name = d.dataset_name
       );
    FOREACH target IN ARRAY p_datasets LOOP
        INSERT INTO public.analytic_app_manifest_datasets
            (app_name, manifest_digest, dataset_name)
        VALUES (p_app_name, p_manifest_digest, target)
        ON CONFLICT DO NOTHING;
    END LOOP;

    RETURN p_manifest_digest;
END
$register$;

ALTER FUNCTION public.register_workspace_app_manifest(TEXT, TEXT, TEXT[], TEXT)
    OWNER TO omega_app_grants_owner;
REVOKE ALL ON FUNCTION public.register_workspace_app_manifest(TEXT, TEXT, TEXT[], TEXT)
    FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.register_workspace_app_manifest(TEXT, TEXT, TEXT[], TEXT)
    TO omega_console;


-- ── workspace publication reconciliation ───────────────────────────────────
-- Mirrors the packaged reconciler: revoke-first, event rows, everything
-- resolved server-side; no cartridge-installation requirement.
CREATE OR REPLACE FUNCTION public.reconcile_workspace_app_dataset_grants(
    p_app_name TEXT
) RETURNS TABLE (dataset_name TEXT, action TEXT)
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
AS $reconcile_workspace$
DECLARE
    scoped_tenant UUID;
    scoped_workspace UUID;
    resolved_cartridge TEXT;
    resolved_digest TEXT;
    app_owner BIGINT;
    app_scope_status TEXT;
    app_tenant UUID;
    app_workspace UUID;
    target TEXT;
    new_id BIGINT;
    stale RECORD;
    actor CONSTANT TEXT := 'server:workspace_publication';
BEGIN
    scoped_tenant := NULLIF(current_setting('app.tenant_id', TRUE), '')::UUID;
    scoped_workspace := NULLIF(current_setting('app.workspace_id', TRUE), '')::UUID;
    IF scoped_tenant IS NULL OR scoped_workspace IS NULL THEN
        RAISE EXCEPTION 'app grant scope is unavailable' USING ERRCODE = '42501';
    END IF;
    IF p_app_name IS NULL OR p_app_name !~ '^[a-zA-Z_][a-zA-Z0-9_]*$' THEN
        RAISE EXCEPTION 'app name is invalid' USING ERRCODE = '22023';
    END IF;

    IF EXISTS (
        SELECT 1 FROM public.analytic_app_manifests m
         WHERE m.app_name = p_app_name
           AND m.source = 'packaged_manifest'
    ) THEN
        RAISE EXCEPTION 'app name belongs to a packaged app' USING ERRCODE = '42501';
    END IF;

    SELECT m.cartridge_id, m.manifest_digest
      INTO resolved_cartridge, resolved_digest
      FROM public.analytic_app_manifests m
     WHERE m.app_name = p_app_name
       AND m.revision = 'active'
       AND m.source = 'workspace_publication'
       AND m.tenant_id = scoped_tenant::text
       AND m.workspace_id = scoped_workspace::text;
    IF resolved_cartridge IS NULL THEN
        RETURN;
    END IF;

    SELECT a.created_by_id, a.scope_status, a.tenant_id, a.workspace_id
      INTO app_owner, app_scope_status, app_tenant, app_workspace
      FROM public.analytic_apps a
     WHERE a.name = p_app_name;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'workspace app is not registered' USING ERRCODE = '42501';
    END IF;
    IF app_owner IS NULL
       OR app_scope_status IS DISTINCT FROM 'scoped'
       OR app_tenant IS DISTINCT FROM scoped_tenant
       OR app_workspace IS DISTINCT FROM scoped_workspace THEN
        RAISE EXCEPTION 'app is not a workspace publication for this scope'
            USING ERRCODE = '42501';
    END IF;

    -- Revoke first: a failure part-way leaves the app with less access, never
    -- more.
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
                target, resolved_digest, 'workspace_publication', actor)
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
$reconcile_workspace$;

ALTER FUNCTION public.reconcile_workspace_app_dataset_grants(TEXT)
    OWNER TO omega_app_grants_owner;
REVOKE ALL ON FUNCTION public.reconcile_workspace_app_dataset_grants(TEXT)
    FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.reconcile_workspace_app_dataset_grants(TEXT)
    TO omega_console;


-- ── the authoritative read: packaged branch unchanged + workspace branch ───
CREATE OR REPLACE FUNCTION public.analytic_app_granted_datasets(
    p_app_name TEXT,
    p_manifest_digest TEXT
) RETURNS TABLE (dataset_name TEXT)
LANGUAGE sql STABLE SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
AS $granted$
    SELECT g.dataset_name
      FROM public.analytic_app_dataset_grants g
      JOIN public.analytic_app_manifests m
        ON m.app_name = g.app_name
       AND m.manifest_digest = g.manifest_digest
       AND m.revision = 'active'
      JOIN public.analytic_app_manifest_datasets d
        ON d.app_name = g.app_name
       AND d.manifest_digest = g.manifest_digest
       AND d.dataset_name = g.dataset_name
      JOIN public.cartridge_installations ci
        ON ci.tenant_id = g.tenant_id
       AND ci.workspace_id = g.workspace_id
       AND ci.cartridge_id = g.cartridge_id
       AND ci.status = 'ready'
      JOIN public.datasets ds
        ON ds.tenant_id = g.tenant_id
       AND ds.workspace_id = g.workspace_id
       AND ds.name = g.dataset_name
       AND ds.cartridge = g.cartridge_id
     WHERE g.revoked_at IS NULL
       AND g.app_name = p_app_name
       AND g.manifest_digest = p_manifest_digest
       AND g.tenant_id = NULLIF(current_setting('app.tenant_id', TRUE), '')::UUID
       AND g.workspace_id = NULLIF(current_setting('app.workspace_id', TRUE), '')::UUID
    UNION
    SELECT g.dataset_name
      FROM public.analytic_app_dataset_grants g
      JOIN public.analytic_app_manifests m
        ON m.app_name = g.app_name
       AND m.manifest_digest = g.manifest_digest
       AND m.revision = 'active'
       AND m.source = 'workspace_publication'
       AND m.tenant_id = g.tenant_id::text
       AND m.workspace_id = g.workspace_id::text
      JOIN public.analytic_app_manifest_datasets d
        ON d.app_name = g.app_name
       AND d.manifest_digest = g.manifest_digest
       AND d.dataset_name = g.dataset_name
      JOIN public.datasets ds
        ON ds.tenant_id = g.tenant_id
       AND ds.workspace_id = g.workspace_id
       AND ds.name = g.dataset_name
       AND ds.scope_status = 'scoped'
     WHERE g.revoked_at IS NULL
       AND g.grant_source = 'workspace_publication'
       AND g.app_name = p_app_name
       AND g.manifest_digest = p_manifest_digest
       AND g.tenant_id = NULLIF(current_setting('app.tenant_id', TRUE), '')::UUID
       AND g.workspace_id = NULLIF(current_setting('app.workspace_id', TRUE), '')::UUID
     ORDER BY dataset_name;
$granted$;

ALTER FUNCTION public.analytic_app_granted_datasets(TEXT, TEXT)
    OWNER TO omega_app_grants_owner;
REVOKE ALL ON FUNCTION public.analytic_app_granted_datasets(TEXT, TEXT)
    FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.analytic_app_granted_datasets(TEXT, TEXT)
    TO omega_console, omega_refinement, omega_workspace;


INSERT INTO schema_migrations (filename, applied_at)
VALUES ('99zzzzz_workspace_app_publications.sql', NOW())
ON CONFLICT (filename) DO NOTHING;
