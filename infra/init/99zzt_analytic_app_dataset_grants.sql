-- Server-owned authority for which datasets a published analytic app may read.
--
-- Until now the answer came from the app itself: datasets were scraped out of
-- the stored HTML with a regex, or read from analytic_apps.datasets_used, or
-- taken from runtime payload metadata. All three are attacker-controlled for a
-- published app, so an app could widen its own access simply by mentioning a
-- dataset name. The regex is not even reliable in the benign case — against the
-- packaged apps it yields fragments like 'sale' and 'pnl_men' from template
-- literals, which is what over-broad string matching produces.
--
-- This ledger is the only root of authority. Rows are written by a server-owned
-- SECURITY DEFINER function from reviewed packaged manifests; no application
-- role can INSERT, UPDATE or DELETE. HTML and runtime metadata keep their
-- descriptive value and are still compared against this table to report drift,
-- but they never widen it.
--
-- Scope model matches the rest of the schema: datasets are workspace scoped
-- (datasets UNIQUE(workspace_id, name)) while analytic_apps is keyed globally
-- by name, so a grant is per tenant/workspace/app/dataset and is validated
-- against the dataset row that actually exists in that workspace.

CREATE UNIQUE INDEX IF NOT EXISTS workspaces_tenant_id_id_idx
    ON workspaces (tenant_id, id);

CREATE UNIQUE INDEX IF NOT EXISTS datasets_scope_name_key
    ON datasets (tenant_id, workspace_id, name);

CREATE TABLE IF NOT EXISTS analytic_app_dataset_grants (
    id                BIGSERIAL PRIMARY KEY,
    tenant_id         UUID NOT NULL,
    workspace_id      UUID NOT NULL,
    app_name          TEXT NOT NULL,
    cartridge_id      TEXT NOT NULL,
    dataset_name      TEXT NOT NULL,
    -- sha256 over (app_name, cartridge_id, sorted datasets, reviewed html).
    -- A change to either the manifest or the served HTML produces a new digest,
    -- which strands every earlier grant until the server reconciles.
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
        REFERENCES workspaces (tenant_id, id) ON DELETE CASCADE,
    -- The dataset must exist in this very workspace. A name alone proves
    -- nothing; this is what stops a grant naming another scope's dataset.
    CONSTRAINT analytic_app_dataset_grants_dataset_fk
        FOREIGN KEY (tenant_id, workspace_id, dataset_name)
        REFERENCES datasets (tenant_id, workspace_id, name) ON DELETE CASCADE,
    CONSTRAINT analytic_app_dataset_grants_app_fk
        FOREIGN KEY (app_name) REFERENCES analytic_apps (name) ON DELETE CASCADE,
    CONSTRAINT analytic_app_dataset_grants_actor_fk
        FOREIGN KEY (granted_by_user_id) REFERENCES users (id) ON DELETE SET NULL,
    CONSTRAINT analytic_app_dataset_grants_source_check
        CHECK (grant_source IN ('packaged_manifest', 'explicit_approval')),
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

-- One live grant per scope + app + dataset + digest. Revoked rows stay for the
-- audit trail, so the uniqueness only covers rows that still authorise.
CREATE UNIQUE INDEX IF NOT EXISTS analytic_app_dataset_grants_live_key
    ON analytic_app_dataset_grants (
        tenant_id, workspace_id, app_name, dataset_name, manifest_digest
    ) WHERE revoked_at IS NULL;

CREATE INDEX IF NOT EXISTS analytic_app_dataset_grants_lookup_idx
    ON analytic_app_dataset_grants (
        tenant_id, workspace_id, app_name, manifest_digest
    ) WHERE revoked_at IS NULL;

-- The cartridge the grant claims must be the cartridge the app actually
-- belongs to, and the dataset must come from that same cartridge. Expressed as
-- a trigger because a CHECK cannot reach other tables.
CREATE OR REPLACE FUNCTION analytic_app_dataset_grant_cartridge_guard()
RETURNS TRIGGER
LANGUAGE plpgsql
SET search_path = pg_catalog, public
AS $guard$
DECLARE
    app_cartridge TEXT;
    dataset_cartridge TEXT;
BEGIN
    SELECT a.cartridge_id INTO app_cartridge
      FROM analytic_apps a WHERE a.name = NEW.app_name;
    IF app_cartridge IS NULL OR app_cartridge <> NEW.cartridge_id THEN
        RAISE EXCEPTION 'grant cartridge does not match the app'
            USING ERRCODE = '23514';
    END IF;

    SELECT d.cartridge INTO dataset_cartridge
      FROM datasets d
     WHERE d.tenant_id = NEW.tenant_id
       AND d.workspace_id = NEW.workspace_id
       AND d.name = NEW.dataset_name;
    IF dataset_cartridge IS NULL OR dataset_cartridge <> NEW.cartridge_id THEN
        RAISE EXCEPTION 'grant dataset does not belong to the cartridge'
            USING ERRCODE = '23514';
    END IF;

    -- The cartridge must be installed and usable in this workspace.
    IF NOT EXISTS (
        SELECT 1 FROM cartridge_installations ci
         WHERE ci.tenant_id = NEW.tenant_id
           AND ci.workspace_id = NEW.workspace_id
           AND ci.cartridge_id = NEW.cartridge_id
           AND ci.status = 'ready'
    ) THEN
        RAISE EXCEPTION 'cartridge is not installed for this workspace'
            USING ERRCODE = '23514';
    END IF;
    RETURN NEW;
END
$guard$;

DROP TRIGGER IF EXISTS analytic_app_dataset_grants_cartridge_guard
    ON analytic_app_dataset_grants;
CREATE TRIGGER analytic_app_dataset_grants_cartridge_guard
    BEFORE INSERT OR UPDATE ON analytic_app_dataset_grants
    FOR EACH ROW EXECUTE FUNCTION analytic_app_dataset_grant_cartridge_guard();

ALTER TABLE analytic_app_dataset_grants ENABLE ROW LEVEL SECURITY;
ALTER TABLE analytic_app_dataset_grants FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS analytic_app_dataset_grants_scope_read
    ON analytic_app_dataset_grants;
CREATE POLICY analytic_app_dataset_grants_scope_read
    ON analytic_app_dataset_grants
    FOR SELECT TO omega_console, omega_refinement, omega_workspace
    USING (omega_rls_workspace_matches(tenant_id, workspace_id));

REVOKE ALL ON analytic_app_dataset_grants FROM PUBLIC;
REVOKE INSERT, UPDATE, DELETE, TRUNCATE ON analytic_app_dataset_grants
    FROM omega_console, omega_refinement, omega_workspace,
         omega_mcp_infra, omega_airflow_dag;
GRANT SELECT ON analytic_app_dataset_grants
    TO omega_console, omega_refinement, omega_workspace;
REVOKE ALL ON SEQUENCE analytic_app_dataset_grants_id_seq FROM PUBLIC;

-- Audit trail. Written by the same definer functions; readable in scope.
CREATE TABLE IF NOT EXISTS analytic_app_dataset_grant_events (
    id            BIGSERIAL PRIMARY KEY,
    grant_id      BIGINT NOT NULL
        REFERENCES analytic_app_dataset_grants (id) ON DELETE CASCADE,
    tenant_id     UUID NOT NULL,
    workspace_id  UUID NOT NULL,
    event         TEXT NOT NULL,
    actor         TEXT NOT NULL,
    detail        TEXT,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    CONSTRAINT analytic_app_dataset_grant_events_event_check
        CHECK (event IN ('granted', 'revoked'))
);

ALTER TABLE analytic_app_dataset_grant_events ENABLE ROW LEVEL SECURITY;
ALTER TABLE analytic_app_dataset_grant_events FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS analytic_app_dataset_grant_events_scope_read
    ON analytic_app_dataset_grant_events;
CREATE POLICY analytic_app_dataset_grant_events_scope_read
    ON analytic_app_dataset_grant_events
    FOR SELECT TO omega_console, omega_refinement, omega_workspace
    USING (omega_rls_workspace_matches(tenant_id, workspace_id));

REVOKE ALL ON analytic_app_dataset_grant_events FROM PUBLIC;
REVOKE INSERT, UPDATE, DELETE, TRUNCATE ON analytic_app_dataset_grant_events
    FROM omega_console, omega_refinement, omega_workspace,
         omega_mcp_infra, omega_airflow_dag;
GRANT SELECT ON analytic_app_dataset_grant_events
    TO omega_console, omega_refinement, omega_workspace;
REVOKE ALL ON SEQUENCE analytic_app_dataset_grant_events_id_seq FROM PUBLIC;


-- Reconcile one packaged app in the caller's scope against a reviewed
-- manifest. The dataset list and digest are supplied by the server from the
-- packaged manifest; nothing about the request comes from the app.
--
-- Idempotent: re-running with the same digest is a no-op. Running with a new
-- digest revokes every grant made under the old one, so a modified app loses
-- its access until the server reconciles it again — there is no path by which
-- editing an app re-approves it.
CREATE OR REPLACE FUNCTION reconcile_analytic_app_dataset_grants(
    p_app_name TEXT,
    p_cartridge_id TEXT,
    p_datasets TEXT[],
    p_manifest_digest TEXT,
    p_granted_by TEXT DEFAULT 'server:packaged_manifest'
) RETURNS TABLE (dataset_name TEXT, action TEXT)
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, public
AS $reconcile$
DECLARE
    scoped_tenant UUID;
    scoped_workspace UUID;
    target TEXT;
    new_id BIGINT;
    stale RECORD;
BEGIN
    scoped_tenant := NULLIF(current_setting('app.tenant_id', TRUE), '')::UUID;
    scoped_workspace := NULLIF(current_setting('app.workspace_id', TRUE), '')::UUID;
    IF scoped_tenant IS NULL OR scoped_workspace IS NULL THEN
        RAISE EXCEPTION 'app grant scope is unavailable' USING ERRCODE = '42501';
    END IF;
    IF p_manifest_digest IS NULL OR p_manifest_digest !~ '^[0-9a-f]{64}$' THEN
        RAISE EXCEPTION 'app manifest digest is invalid' USING ERRCODE = '22023';
    END IF;

    -- Anything granted under a different digest no longer reflects what is
    -- served. Revoke before granting so a failure mid-way leaves the app with
    -- less access, never more.
    FOR stale IN
        SELECT g.id, g.dataset_name
          FROM analytic_app_dataset_grants g
         WHERE g.tenant_id = scoped_tenant
           AND g.workspace_id = scoped_workspace
           AND g.app_name = p_app_name
           AND g.revoked_at IS NULL
           AND (g.manifest_digest <> p_manifest_digest
                OR NOT (g.dataset_name = ANY (p_datasets)))
    LOOP
        UPDATE analytic_app_dataset_grants
           SET revoked_at = clock_timestamp(),
               revoked_by = p_granted_by,
               revoke_reason = 'manifest_reconciliation'
         WHERE id = stale.id;
        INSERT INTO analytic_app_dataset_grant_events
            (grant_id, tenant_id, workspace_id, event, actor, detail)
        VALUES (stale.id, scoped_tenant, scoped_workspace, 'revoked',
                p_granted_by, 'superseded by ' || p_manifest_digest);
        dataset_name := stale.dataset_name;
        action := 'revoked';
        RETURN NEXT;
    END LOOP;

    IF p_datasets IS NULL THEN
        RETURN;
    END IF;

    FOREACH target IN ARRAY p_datasets LOOP
        -- Skip datasets that do not exist in this workspace rather than
        -- failing the whole reconciliation: a workspace may legitimately have
        -- only part of a cartridge's Gold materialised.
        CONTINUE WHEN NOT EXISTS (
            SELECT 1 FROM datasets d
             WHERE d.tenant_id = scoped_tenant
               AND d.workspace_id = scoped_workspace
               AND d.name = target
        );
        CONTINUE WHEN EXISTS (
            SELECT 1 FROM analytic_app_dataset_grants g
             WHERE g.tenant_id = scoped_tenant
               AND g.workspace_id = scoped_workspace
               AND g.app_name = p_app_name
               AND g.dataset_name = target
               AND g.manifest_digest = p_manifest_digest
               AND g.revoked_at IS NULL
        );
        INSERT INTO analytic_app_dataset_grants
            (tenant_id, workspace_id, app_name, cartridge_id, dataset_name,
             manifest_digest, grant_source, granted_by)
        VALUES (scoped_tenant, scoped_workspace, p_app_name, p_cartridge_id,
                target, p_manifest_digest, 'packaged_manifest', p_granted_by)
        RETURNING id INTO new_id;
        INSERT INTO analytic_app_dataset_grant_events
            (grant_id, tenant_id, workspace_id, event, actor, detail)
        VALUES (new_id, scoped_tenant, scoped_workspace, 'granted',
                p_granted_by, p_manifest_digest);
        dataset_name := target;
        action := 'granted';
        RETURN NEXT;
    END LOOP;
END
$reconcile$;

REVOKE ALL ON FUNCTION reconcile_analytic_app_dataset_grants(
    TEXT, TEXT, TEXT[], TEXT, TEXT) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION reconcile_analytic_app_dataset_grants(
    TEXT, TEXT, TEXT[], TEXT, TEXT) TO omega_console;


-- Explicit revocation, in scope, server-owned.
CREATE OR REPLACE FUNCTION revoke_analytic_app_dataset_grant(
    p_app_name TEXT,
    p_dataset_name TEXT,
    p_revoked_by TEXT DEFAULT 'server:revocation',
    p_reason TEXT DEFAULT 'explicit_revocation'
) RETURNS BIGINT
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, public
AS $revoke$
DECLARE
    scoped_tenant UUID;
    scoped_workspace UUID;
    affected BIGINT := 0;
    row_id BIGINT;
BEGIN
    scoped_tenant := NULLIF(current_setting('app.tenant_id', TRUE), '')::UUID;
    scoped_workspace := NULLIF(current_setting('app.workspace_id', TRUE), '')::UUID;
    IF scoped_tenant IS NULL OR scoped_workspace IS NULL THEN
        RAISE EXCEPTION 'app grant scope is unavailable' USING ERRCODE = '42501';
    END IF;

    FOR row_id IN
        SELECT g.id FROM analytic_app_dataset_grants g
         WHERE g.tenant_id = scoped_tenant
           AND g.workspace_id = scoped_workspace
           AND g.app_name = p_app_name
           AND g.dataset_name = p_dataset_name
           AND g.revoked_at IS NULL
    LOOP
        UPDATE analytic_app_dataset_grants
           SET revoked_at = clock_timestamp(),
               revoked_by = p_revoked_by,
               revoke_reason = p_reason
         WHERE id = row_id;
        INSERT INTO analytic_app_dataset_grant_events
            (grant_id, tenant_id, workspace_id, event, actor, detail)
        VALUES (row_id, scoped_tenant, scoped_workspace, 'revoked',
                p_revoked_by, p_reason);
        affected := affected + 1;
    END LOOP;
    RETURN affected;
END
$revoke$;

REVOKE ALL ON FUNCTION revoke_analytic_app_dataset_grant(TEXT, TEXT, TEXT, TEXT)
    FROM PUBLIC;
GRANT EXECUTE ON FUNCTION revoke_analytic_app_dataset_grant(TEXT, TEXT, TEXT, TEXT)
    TO omega_console;
