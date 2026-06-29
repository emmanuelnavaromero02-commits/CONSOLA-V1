-- Live Copilot console context.
--
-- Stores a workspace-scoped operational cut assembled from the main console
-- surfaces plus deduplicated recommendations. The rows are aggregate/contextual
-- only; no raw employee roster, salaries, secrets, or tool payloads.

CREATE TABLE IF NOT EXISTS copilot_context_snapshots (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id       UUID REFERENCES tenants(id) ON DELETE CASCADE,
    workspace_id    UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    status          TEXT NOT NULL DEFAULT 'ready'
        CHECK (status IN ('ready', 'partial', 'blocked', 'failed')),
    summary         JSONB NOT NULL DEFAULT '{}'::jsonb,
    sources         JSONB NOT NULL DEFAULT '[]'::jsonb,
    metrics         JSONB NOT NULL DEFAULT '{}'::jsonb,
    errors          JSONB NOT NULL DEFAULT '[]'::jsonb,
    generated_by    TEXT NOT NULL DEFAULT 'copilot_context_scheduler',
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS copilot_context_snapshots_workspace_created_idx
    ON copilot_context_snapshots(workspace_id, created_at DESC);

CREATE INDEX IF NOT EXISTS copilot_context_snapshots_tenant_workspace_idx
    ON copilot_context_snapshots(tenant_id, workspace_id);

CREATE TABLE IF NOT EXISTS copilot_recommendations (
    id                   UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id            UUID REFERENCES tenants(id) ON DELETE CASCADE,
    workspace_id         UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    snapshot_id          UUID REFERENCES copilot_context_snapshots(id) ON DELETE SET NULL,
    fingerprint          TEXT NOT NULL,
    severity             TEXT NOT NULL DEFAULT 'info'
        CHECK (severity IN ('critical', 'warning', 'info', 'success')),
    category             TEXT NOT NULL,
    title                TEXT NOT NULL,
    body                 TEXT NOT NULL,
    evidence             JSONB NOT NULL DEFAULT '{}'::jsonb,
    action_label         TEXT,
    action_href          TEXT,
    action_kind          TEXT NOT NULL DEFAULT 'navigate',
    required_permission  TEXT,
    status               TEXT NOT NULL DEFAULT 'active'
        CHECK (status IN ('active', 'seen', 'dismissed', 'resolved', 'superseded')),
    first_seen_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_seen_at         TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    resolved_at          TIMESTAMPTZ,
    dismissed_at         TIMESTAMPTZ,
    UNIQUE (workspace_id, fingerprint)
);

CREATE INDEX IF NOT EXISTS copilot_recommendations_workspace_status_idx
    ON copilot_recommendations(workspace_id, status, severity, last_seen_at DESC);

CREATE INDEX IF NOT EXISTS copilot_recommendations_tenant_workspace_idx
    ON copilot_recommendations(tenant_id, workspace_id);

DO $$
DECLARE
    scoped_roles text;
    owner_roles text;
    tbl text;
BEGIN
    SELECT string_agg(quote_ident(rolname), ', ')
      INTO scoped_roles
      FROM pg_roles
     WHERE rolname = ANY(ARRAY['omega_workspace', 'omega_mcp_infra']);

    SELECT string_agg(quote_ident(rolname), ', ')
      INTO owner_roles
      FROM pg_roles
     WHERE rolname = ANY(ARRAY['omega_console', 'omega_refinement']);

    FOREACH tbl IN ARRAY ARRAY['copilot_context_snapshots', 'copilot_recommendations']
    LOOP
        EXECUTE format('ALTER TABLE public.%I ENABLE ROW LEVEL SECURITY', tbl);
        EXECUTE format('ALTER TABLE public.%I FORCE ROW LEVEL SECURITY', tbl);

        EXECUTE format('DROP POLICY IF EXISTS %I ON public.%I', tbl || '_workspace_rls', tbl);
        IF scoped_roles IS NOT NULL THEN
            EXECUTE format(
                'CREATE POLICY %I ON public.%I
                   FOR ALL TO %s
                   USING (omega_rls_workspace_matches(tenant_id, workspace_id))
                   WITH CHECK (omega_rls_workspace_matches(tenant_id, workspace_id))',
                tbl || '_workspace_rls',
                tbl,
                scoped_roles
            );
        END IF;

        EXECUTE format('DROP POLICY IF EXISTS %I ON public.%I', tbl || '_owner_rls', tbl);
        IF owner_roles IS NOT NULL THEN
            EXECUTE format(
                'CREATE POLICY %I ON public.%I
                   FOR ALL TO %s
                   USING (true)
                   WITH CHECK (true)',
                tbl || '_owner_rls',
                tbl,
                owner_roles
            );
        END IF;
    END LOOP;
END $$;

DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'omega_console') THEN
        GRANT SELECT, INSERT, UPDATE, DELETE ON copilot_context_snapshots TO omega_console;
        GRANT SELECT, INSERT, UPDATE, DELETE ON copilot_recommendations TO omega_console;
    END IF;
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'omega_workspace') THEN
        GRANT SELECT ON copilot_context_snapshots TO omega_workspace;
        GRANT SELECT, UPDATE ON copilot_recommendations TO omega_workspace;
    END IF;
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'omega_mcp_infra') THEN
        GRANT SELECT ON copilot_context_snapshots TO omega_mcp_infra;
        GRANT SELECT ON copilot_recommendations TO omega_mcp_infra;
    END IF;
END $$;

INSERT INTO schema_migrations(filename, applied_at)
VALUES ('99zo_copilot_live_context.sql', NOW())
ON CONFLICT (filename) DO NOTHING;
