-- Server-owned authority for which datasets a published analytic app may read.
--
-- Until now the answer came from the app itself: datasets were scraped out of
-- the stored HTML with a regex, or read from analytic_apps.datasets_used, or
-- taken from runtime payload metadata. All three are attacker-controlled for a
-- published app. The regex is not even reliable in the benign case — against
-- the packaged apps it yields fragments like 'sale' and 'pnl_men'.
--
-- Three properties make this ledger trustworthy, and each closes a specific
-- attack that was demonstrated against an earlier draft of this file:
--
--  1. The reconciliation function takes only an app name. It resolves the
--     cartridge, the digest and the dataset list from analytic_app_manifests,
--     which is generated from the reviewed manifests baked into the image. A
--     caller that could pass p_datasets, p_cartridge_id or p_granted_by could
--     grant itself anything, so those parameters are gone.
--
--  2. Every relation is schema-qualified and search_path is
--     `pg_catalog, pg_temp` with public deliberately absent. PostgreSQL
--     searches the temporary schema *first* for relations when pg_temp is not
--     listed, so `search_path = pg_catalog, public` let any caller shadow
--     public.datasets, public.analytic_apps and public.cartridge_installations
--     with temp tables and drive the guard to approve anything. Listing
--     pg_temp explicitly last removes the implicit priority; qualifying every
--     name removes the dependency on search_path altogether.
--
--  3. The functions are owned by omega_app_grants_owner — NOLOGIN,
--     NOSUPERUSER, NOBYPASSRLS — not by postgres. A SECURITY DEFINER function
--     owned by a superuser executes as one, so shadowing it was a privilege
--     escalation and not merely a logic bug.
--
-- Scope model matches the rest of the schema: datasets are workspace scoped
-- while analytic_apps is keyed globally by name, so a grant is per
-- tenant/workspace/app/dataset, validated against the dataset row that
-- actually exists in that workspace.

CREATE UNIQUE INDEX IF NOT EXISTS workspaces_tenant_id_id_idx
    ON public.workspaces (tenant_id, id);

CREATE UNIQUE INDEX IF NOT EXISTS datasets_scope_name_key
    ON public.datasets (tenant_id, workspace_id, name);


-- ── dedicated owner ────────────────────────────────────────────────────────
-- Least privilege for the definer identity: it may read what it must check and
-- write only the ledger. It cannot log in, cannot create roles or databases,
-- and does not bypass RLS.
DO $owner_role$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_catalog.pg_roles
                    WHERE rolname = 'omega_app_grants_owner') THEN
        CREATE ROLE omega_app_grants_owner
            NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOREPLICATION;
    END IF;
END
$owner_role$;

ALTER ROLE omega_app_grants_owner
    NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS;

GRANT USAGE ON SCHEMA public TO omega_app_grants_owner;


-- ── manifest registry (contents loaded by 99zzu) ───────────────────────────
CREATE TABLE IF NOT EXISTS public.analytic_app_manifests (
    app_name        TEXT PRIMARY KEY,
    cartridge_id    TEXT NOT NULL,
    manifest_digest CHAR(64) NOT NULL,
    html_sha256     CHAR(64) NOT NULL,
    revision        TEXT NOT NULL DEFAULT 'active',
    source          TEXT NOT NULL DEFAULT 'packaged_manifest',
    generated_at    TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    CONSTRAINT analytic_app_manifests_source_check
        CHECK (source = 'packaged_manifest'),
    CONSTRAINT analytic_app_manifests_revision_check
        CHECK (revision IN ('active', 'superseded')),
    CONSTRAINT analytic_app_manifests_digest_check
        CHECK (manifest_digest ~ '^[0-9a-f]{64}$'
               AND html_sha256 ~ '^[0-9a-f]{64}$'),
    CONSTRAINT analytic_app_manifests_names_check
        CHECK (app_name ~ '^[a-zA-Z_][a-zA-Z0-9_]*$'
               AND cartridge_id ~ '^[a-z][a-z0-9_]*$')
);

CREATE TABLE IF NOT EXISTS public.analytic_app_manifest_datasets (
    app_name        TEXT NOT NULL
        REFERENCES public.analytic_app_manifests (app_name) ON DELETE CASCADE,
    manifest_digest CHAR(64) NOT NULL,
    dataset_name    TEXT NOT NULL,
    PRIMARY KEY (app_name, manifest_digest, dataset_name),
    CONSTRAINT analytic_app_manifest_datasets_name_check
        CHECK (dataset_name ~ '^[a-zA-Z_][a-zA-Z0-9_]*$')
);

CREATE INDEX IF NOT EXISTS analytic_app_manifest_datasets_lookup_idx
    ON public.analytic_app_manifest_datasets (app_name, manifest_digest);

REVOKE ALL ON public.analytic_app_manifests FROM PUBLIC;
REVOKE ALL ON public.analytic_app_manifest_datasets FROM PUBLIC;
GRANT SELECT ON public.analytic_app_manifests,
                public.analytic_app_manifest_datasets
    TO omega_console, omega_refinement, omega_workspace, omega_app_grants_owner;


-- ── the ledger ─────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS public.analytic_app_dataset_grants (
    id                BIGSERIAL PRIMARY KEY,
    tenant_id         UUID NOT NULL,
    workspace_id      UUID NOT NULL,
    app_name          TEXT NOT NULL,
    cartridge_id      TEXT NOT NULL,
    dataset_name      TEXT NOT NULL,
    manifest_digest   CHAR(64) NOT NULL,
    grant_source      TEXT NOT NULL,
    granted_at        TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    granted_by        TEXT NOT NULL,
    granted_by_user_id BIGINT,
    revoked_at        TIMESTAMPTZ,
    revoked_by        TEXT,
    revoke_reason     TEXT,
    recorded_by_server BOOLEAN NOT NULL DEFAULT TRUE,
    CONSTRAINT analytic_app_dataset_grants_scope_fk
        FOREIGN KEY (tenant_id, workspace_id)
        REFERENCES public.workspaces (tenant_id, id) ON DELETE CASCADE,
    CONSTRAINT analytic_app_dataset_grants_dataset_fk
        FOREIGN KEY (tenant_id, workspace_id, dataset_name)
        REFERENCES public.datasets (tenant_id, workspace_id, name) ON DELETE CASCADE,
    CONSTRAINT analytic_app_dataset_grants_app_fk
        FOREIGN KEY (app_name)
        REFERENCES public.analytic_apps (name) ON DELETE CASCADE,
    CONSTRAINT analytic_app_dataset_grants_manifest_fk
        FOREIGN KEY (app_name, manifest_digest, dataset_name)
        REFERENCES public.analytic_app_manifest_datasets
                   (app_name, manifest_digest, dataset_name) ON DELETE RESTRICT,
    CONSTRAINT analytic_app_dataset_grants_actor_fk
        FOREIGN KEY (granted_by_user_id)
        REFERENCES public.users (id) ON DELETE SET NULL,
    CONSTRAINT analytic_app_dataset_grants_source_check
        CHECK (grant_source = 'packaged_manifest'),
    CONSTRAINT analytic_app_dataset_grants_actor_check
        CHECK (granted_by = 'server:packaged_manifest'),
    CONSTRAINT analytic_app_dataset_grants_digest_check
        CHECK (manifest_digest ~ '^[0-9a-f]{64}$'),
    CONSTRAINT analytic_app_dataset_grants_names_check
        CHECK (app_name ~ '^[a-zA-Z_][a-zA-Z0-9_]*$'
               AND dataset_name ~ '^[a-zA-Z_][a-zA-Z0-9_]*$'
               AND cartridge_id ~ '^[a-z][a-z0-9_]*$'),
    CONSTRAINT analytic_app_dataset_grants_server_check
        CHECK (recorded_by_server = TRUE),
    CONSTRAINT analytic_app_dataset_grants_revocation_check
        CHECK ((revoked_at IS NULL AND revoked_by IS NULL)
               OR (revoked_at IS NOT NULL AND revoked_by IS NOT NULL))
);

-- The manifest FK is the structural half of "no self-authorisation": a row can
-- only name a (app, digest, dataset) triple the registry already carries, so
-- even a caller who reached the table directly could not invent one.
ALTER TABLE public.analytic_app_dataset_grants
    DROP CONSTRAINT IF EXISTS analytic_app_dataset_grants_manifest_fk;
ALTER TABLE public.analytic_app_dataset_grants
    ADD CONSTRAINT analytic_app_dataset_grants_manifest_fk
    FOREIGN KEY (app_name, manifest_digest, dataset_name)
    REFERENCES public.analytic_app_manifest_datasets
               (app_name, manifest_digest, dataset_name) ON DELETE RESTRICT;

ALTER TABLE public.analytic_app_dataset_grants
    DROP CONSTRAINT IF EXISTS analytic_app_dataset_grants_source_check;
ALTER TABLE public.analytic_app_dataset_grants
    ADD CONSTRAINT analytic_app_dataset_grants_source_check
    CHECK (grant_source = 'packaged_manifest');

ALTER TABLE public.analytic_app_dataset_grants
    DROP CONSTRAINT IF EXISTS analytic_app_dataset_grants_actor_check;
ALTER TABLE public.analytic_app_dataset_grants
    ADD CONSTRAINT analytic_app_dataset_grants_actor_check
    CHECK (granted_by = 'server:packaged_manifest');

CREATE UNIQUE INDEX IF NOT EXISTS analytic_app_dataset_grants_live_key
    ON public.analytic_app_dataset_grants (
        tenant_id, workspace_id, app_name, dataset_name, manifest_digest
    ) WHERE revoked_at IS NULL;

CREATE INDEX IF NOT EXISTS analytic_app_dataset_grants_lookup_idx
    ON public.analytic_app_dataset_grants (
        tenant_id, workspace_id, app_name, manifest_digest
    ) WHERE revoked_at IS NULL;

CREATE INDEX IF NOT EXISTS analytic_app_dataset_grants_cartridge_idx
    ON public.analytic_app_dataset_grants (
        tenant_id, workspace_id, cartridge_id
    ) WHERE revoked_at IS NULL;

CREATE TABLE IF NOT EXISTS public.analytic_app_dataset_grant_events (
    id            BIGSERIAL PRIMARY KEY,
    grant_id      BIGINT NOT NULL
        REFERENCES public.analytic_app_dataset_grants (id) ON DELETE CASCADE,
    tenant_id     UUID NOT NULL,
    workspace_id  UUID NOT NULL,
    event         TEXT NOT NULL,
    actor         TEXT NOT NULL,
    detail        TEXT,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    CONSTRAINT analytic_app_dataset_grant_events_event_check
        CHECK (event IN ('granted', 'revoked'))
);


-- ── RLS ────────────────────────────────────────────────────────────────────
-- FORCE applies to the table owner too, so the definer identity is scoped by
-- the same policy as everyone else and cannot read across workspaces.
ALTER TABLE public.analytic_app_dataset_grants ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.analytic_app_dataset_grants FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS analytic_app_dataset_grants_scope_read
    ON public.analytic_app_dataset_grants;
CREATE POLICY analytic_app_dataset_grants_scope_read
    ON public.analytic_app_dataset_grants
    FOR SELECT TO omega_console, omega_refinement, omega_workspace,
                  omega_app_grants_owner
    USING (public.omega_rls_workspace_matches(tenant_id, workspace_id));
DROP POLICY IF EXISTS analytic_app_dataset_grants_owner_write
    ON public.analytic_app_dataset_grants;
CREATE POLICY analytic_app_dataset_grants_owner_write
    ON public.analytic_app_dataset_grants
    FOR ALL TO omega_app_grants_owner
    USING (public.omega_rls_workspace_matches(tenant_id, workspace_id))
    WITH CHECK (public.omega_rls_workspace_matches(tenant_id, workspace_id));

ALTER TABLE public.analytic_app_dataset_grant_events ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.analytic_app_dataset_grant_events FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS analytic_app_dataset_grant_events_scope_read
    ON public.analytic_app_dataset_grant_events;
CREATE POLICY analytic_app_dataset_grant_events_scope_read
    ON public.analytic_app_dataset_grant_events
    FOR SELECT TO omega_console, omega_refinement, omega_workspace,
                  omega_app_grants_owner
    USING (public.omega_rls_workspace_matches(tenant_id, workspace_id));
DROP POLICY IF EXISTS analytic_app_dataset_grant_events_owner_write
    ON public.analytic_app_dataset_grant_events;
CREATE POLICY analytic_app_dataset_grant_events_owner_write
    ON public.analytic_app_dataset_grant_events
    FOR ALL TO omega_app_grants_owner
    USING (public.omega_rls_workspace_matches(tenant_id, workspace_id))
    WITH CHECK (public.omega_rls_workspace_matches(tenant_id, workspace_id));


-- ── ACL ────────────────────────────────────────────────────────────────────
REVOKE ALL ON public.analytic_app_dataset_grants FROM PUBLIC;
REVOKE ALL ON public.analytic_app_dataset_grant_events FROM PUBLIC;
REVOKE INSERT, UPDATE, DELETE, TRUNCATE
    ON public.analytic_app_dataset_grants, public.analytic_app_dataset_grant_events
    FROM omega_console, omega_refinement, omega_workspace,
         omega_mcp_infra, omega_airflow_dag;
GRANT SELECT ON public.analytic_app_dataset_grants,
                public.analytic_app_dataset_grant_events
    TO omega_console, omega_refinement, omega_workspace;
GRANT SELECT, INSERT, UPDATE ON public.analytic_app_dataset_grants,
                                public.analytic_app_dataset_grant_events
    TO omega_app_grants_owner;
GRANT SELECT ON public.datasets, public.analytic_apps,
                public.cartridge_installations, public.workspaces, public.users
    TO omega_app_grants_owner;
REVOKE ALL ON SEQUENCE public.analytic_app_dataset_grants_id_seq FROM PUBLIC;
REVOKE ALL ON SEQUENCE public.analytic_app_dataset_grant_events_id_seq FROM PUBLIC;
GRANT USAGE ON SEQUENCE public.analytic_app_dataset_grants_id_seq,
                        public.analytic_app_dataset_grant_events_id_seq
    TO omega_app_grants_owner;

ALTER TABLE public.analytic_app_dataset_grants OWNER TO omega_app_grants_owner;
ALTER TABLE public.analytic_app_dataset_grant_events OWNER TO omega_app_grants_owner;


-- ── reconciliation ─────────────────────────────────────────────────────────
-- Takes an app name and nothing else. Everything that decides the outcome —
-- cartridge, digest, dataset list, actor — is resolved here from the registry.
--
-- p_expected_digest is advisory: the caller may state which revision it
-- believes is current, and a mismatch aborts. It never selects the datasets.
CREATE OR REPLACE FUNCTION public.reconcile_analytic_app_dataset_grants(
    p_app_name TEXT,
    p_expected_digest TEXT DEFAULT NULL
) RETURNS TABLE (dataset_name TEXT, action TEXT)
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
AS $reconcile$
DECLARE
    scoped_tenant UUID;
    scoped_workspace UUID;
    resolved_cartridge TEXT;
    resolved_digest TEXT;
    app_owner BIGINT;
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

    -- Packaged and active, or nothing. A user-created app has no registry row
    -- and leaves here with no grants.
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

    -- A user-created app must never pick up a packaged manifest's grants by
    -- sharing its name.
    SELECT a.created_by_id INTO app_owner
      FROM public.analytic_apps a WHERE a.name = p_app_name;
    IF app_owner IS NOT NULL THEN
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
        -- A workspace may legitimately have only part of a cartridge's Gold
        -- materialised; that is an absent grant, not an error.
        CONTINUE WHEN NOT EXISTS (
            SELECT 1 FROM public.datasets ds
             WHERE ds.tenant_id = scoped_tenant
               AND ds.workspace_id = scoped_workspace
               AND ds.name = target
               AND ds.cartridge = resolved_cartridge
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
$reconcile$;

ALTER FUNCTION public.reconcile_analytic_app_dataset_grants(TEXT, TEXT)
    OWNER TO omega_app_grants_owner;
REVOKE ALL ON FUNCTION public.reconcile_analytic_app_dataset_grants(TEXT, TEXT)
    FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.reconcile_analytic_app_dataset_grants(TEXT, TEXT)
    TO omega_console;


-- ── revocation ─────────────────────────────────────────────────────────────
-- Everything a cartridge granted in this scope, in one statement. Called when
-- an installation stops being ready, in that same transaction.
CREATE OR REPLACE FUNCTION public.revoke_analytic_app_cartridge_grants(
    p_cartridge_id TEXT,
    p_reason TEXT DEFAULT 'installation_not_ready'
) RETURNS BIGINT
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
AS $revoke_cartridge$
DECLARE
    scoped_tenant UUID;
    scoped_workspace UUID;
    affected BIGINT := 0;
    row_id BIGINT;
    actor CONSTANT TEXT := 'server:packaged_manifest';
BEGIN
    scoped_tenant := NULLIF(current_setting('app.tenant_id', TRUE), '')::UUID;
    scoped_workspace := NULLIF(current_setting('app.workspace_id', TRUE), '')::UUID;
    IF scoped_tenant IS NULL OR scoped_workspace IS NULL THEN
        RAISE EXCEPTION 'app grant scope is unavailable' USING ERRCODE = '42501';
    END IF;

    FOR row_id IN
        SELECT g.id FROM public.analytic_app_dataset_grants g
         WHERE g.tenant_id = scoped_tenant
           AND g.workspace_id = scoped_workspace
           AND g.cartridge_id = p_cartridge_id
           AND g.revoked_at IS NULL
    LOOP
        UPDATE public.analytic_app_dataset_grants
           SET revoked_at = clock_timestamp(),
               revoked_by = actor,
               revoke_reason = p_reason
         WHERE id = row_id;
        INSERT INTO public.analytic_app_dataset_grant_events
            (grant_id, tenant_id, workspace_id, event, actor, detail)
        VALUES (row_id, scoped_tenant, scoped_workspace, 'revoked',
                actor, p_reason);
        affected := affected + 1;
    END LOOP;
    RETURN affected;
END
$revoke_cartridge$;

ALTER FUNCTION public.revoke_analytic_app_cartridge_grants(TEXT, TEXT)
    OWNER TO omega_app_grants_owner;
REVOKE ALL ON FUNCTION public.revoke_analytic_app_cartridge_grants(TEXT, TEXT)
    FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.revoke_analytic_app_cartridge_grants(TEXT, TEXT)
    TO omega_console;


-- ── the authoritative read ─────────────────────────────────────────────────
-- One query answers "may this app read this dataset, right now". Reading the
-- grant and then checking the installation separately is a TOCTOU window: the
-- cartridge can stop being ready between the two.
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
     ORDER BY g.dataset_name;
$granted$;

ALTER FUNCTION public.analytic_app_granted_datasets(TEXT, TEXT)
    OWNER TO omega_app_grants_owner;
REVOKE ALL ON FUNCTION public.analytic_app_granted_datasets(TEXT, TEXT)
    FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.analytic_app_granted_datasets(TEXT, TEXT)
    TO omega_console, omega_refinement, omega_workspace;
