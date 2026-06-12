-- Control Room persistent action contract.
--
-- control_room_action_executions remains as the historical activity table.
-- action_runs/action_run_events are the durable beta contract used to prove
-- dry-run -> execute -> outcome/lesson cycles with tenant/workspace isolation.

CREATE TABLE IF NOT EXISTS action_runs (
    id                BIGSERIAL PRIMARY KEY,
    tenant_id         UUID REFERENCES tenants(id) ON DELETE CASCADE,
    workspace_id      UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    item_id           TEXT NOT NULL,
    decision_id       BIGINT REFERENCES decisions(id) ON DELETE SET NULL,
    legacy_execution_id BIGINT,
    action_type       TEXT NOT NULL,
    adapter_name      TEXT NOT NULL DEFAULT 'internal',
    mode              TEXT NOT NULL,
    status            TEXT NOT NULL,
    risk_level        TEXT NOT NULL DEFAULT 'low',
    requires_approval BOOLEAN NOT NULL DEFAULT TRUE,
    approval_status   TEXT NOT NULL DEFAULT 'implicit_internal_beta',
    idempotency_key   TEXT NOT NULL,
    actor_id          BIGINT REFERENCES users(id) ON DELETE SET NULL,
    actor_email       TEXT,
    input             JSONB NOT NULL DEFAULT '{}'::jsonb,
    dry_run_result    JSONB NOT NULL DEFAULT '{}'::jsonb,
    execution_result  JSONB NOT NULL DEFAULT '{}'::jsonb,
    side_effect       JSONB NOT NULL DEFAULT '{}'::jsonb,
    error_code        TEXT,
    error_message     TEXT,
    metadata          JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    completed_at      TIMESTAMPTZ,
    FOREIGN KEY (workspace_id, item_id)
      REFERENCES control_room_items(workspace_id, item_id)
      ON DELETE CASCADE,
    CONSTRAINT action_runs_mode_chk
      CHECK (mode IN ('preview', 'dry_run', 'execute')),
    CONSTRAINT action_runs_status_chk
      CHECK (status IN ('pending', 'preview_generated', 'dry_run_completed', 'dry_run_failed', 'blocked', 'completed', 'failed'))
);

CREATE UNIQUE INDEX IF NOT EXISTS action_runs_workspace_idempotency_idx
    ON action_runs(workspace_id, idempotency_key);

CREATE INDEX IF NOT EXISTS action_runs_item_idx
    ON action_runs(workspace_id, item_id, created_at DESC);

CREATE INDEX IF NOT EXISTS action_runs_decision_idx
    ON action_runs(workspace_id, decision_id, created_at DESC)
    WHERE decision_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS action_runs_status_idx
    ON action_runs(workspace_id, status, created_at DESC);

CREATE TABLE IF NOT EXISTS action_run_events (
    id             BIGSERIAL PRIMARY KEY,
    tenant_id      UUID REFERENCES tenants(id) ON DELETE CASCADE,
    workspace_id   UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    action_run_id  BIGINT NOT NULL REFERENCES action_runs(id) ON DELETE CASCADE,
    item_id        TEXT NOT NULL,
    event_type     TEXT NOT NULL,
    status         TEXT NOT NULL,
    actor_id       BIGINT REFERENCES users(id) ON DELETE SET NULL,
    actor_email    TEXT,
    metadata       JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS action_run_events_run_idx
    ON action_run_events(workspace_id, action_run_id, created_at DESC);

CREATE INDEX IF NOT EXISTS action_run_events_item_idx
    ON action_run_events(workspace_id, item_id, created_at DESC);

DO $$
DECLARE
    scoped_roles text;
    owner_roles text;
    tbl text;
BEGIN
    SELECT string_agg(quote_ident(rolname), ', ')
      INTO scoped_roles
      FROM pg_roles
     WHERE rolname = ANY(ARRAY[
        'omega_workspace',
        'omega_mcp_infra',
        'omega_airflow_dag'
     ]);

    SELECT string_agg(quote_ident(rolname), ', ')
      INTO owner_roles
      FROM pg_roles
     WHERE rolname = ANY(ARRAY[
        'omega_console',
        'omega_refinement'
     ]);

    FOREACH tbl IN ARRAY ARRAY['action_runs', 'action_run_events']
    LOOP
        EXECUTE format('ALTER TABLE public.%I ENABLE ROW LEVEL SECURITY', tbl);
        EXECUTE format('ALTER TABLE public.%I FORCE ROW LEVEL SECURITY', tbl);

        EXECUTE format('DROP POLICY IF EXISTS %I ON public.%I', tbl || '_tenant_workspace_rls', tbl);
        IF scoped_roles IS NOT NULL THEN
            EXECUTE format(
                'CREATE POLICY %I ON public.%I
                   FOR ALL
                   TO %s
                   USING (omega_rls_workspace_matches(tenant_id, workspace_id))
                   WITH CHECK (omega_rls_workspace_matches(tenant_id, workspace_id))',
                tbl || '_tenant_workspace_rls',
                tbl,
                scoped_roles
            );
        END IF;

        EXECUTE format('DROP POLICY IF EXISTS %I ON public.%I', tbl || '_platform_owner_rls', tbl);
        IF owner_roles IS NOT NULL THEN
            EXECUTE format(
                'CREATE POLICY %I ON public.%I
                   FOR ALL
                   TO %s
                   USING (true)
                   WITH CHECK (true)',
                tbl || '_platform_owner_rls',
                tbl,
                owner_roles
            );
        END IF;
    END LOOP;
END $$;

GRANT SELECT, INSERT, UPDATE ON action_runs TO omega_console;
GRANT SELECT, INSERT, UPDATE ON action_run_events TO omega_console;
GRANT USAGE, SELECT ON SEQUENCE action_runs_id_seq TO omega_console;
GRANT USAGE, SELECT ON SEQUENCE action_run_events_id_seq TO omega_console;

INSERT INTO schema_migrations (filename, applied_at)
VALUES ('99n_control_room_action_runs.sql', NOW())
ON CONFLICT (filename) DO NOTHING;
