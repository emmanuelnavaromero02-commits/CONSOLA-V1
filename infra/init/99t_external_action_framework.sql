-- Prompt 18A: external action framework sandbox.
--
-- This creates the approval/dry-run/sandbox execution contract for future
-- external write-back without enabling any real adapter.

CREATE EXTENSION IF NOT EXISTS pgcrypto;

CREATE TABLE IF NOT EXISTS external_actions (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id           UUID REFERENCES tenants(id) ON DELETE CASCADE,
    workspace_id        UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    source_type         TEXT NOT NULL,
    source_id           TEXT NOT NULL,
    action_type         TEXT NOT NULL,
    adapter_name        TEXT NOT NULL DEFAULT 'sandbox',
    payload             JSONB NOT NULL DEFAULT '{}'::jsonb,
    dry_run_payload     JSONB NOT NULL DEFAULT '{}'::jsonb,
    dry_run_result      JSONB NOT NULL DEFAULT '{}'::jsonb,
    execution_result    JSONB NOT NULL DEFAULT '{}'::jsonb,
    idempotency_key     TEXT NOT NULL,
    status              TEXT NOT NULL DEFAULT 'pending_approval',
    created_by          BIGINT REFERENCES users(id) ON DELETE SET NULL,
    approved_by         BIGINT REFERENCES users(id) ON DELETE SET NULL,
    rejected_by         BIGINT REFERENCES users(id) ON DELETE SET NULL,
    cancelled_by        BIGINT REFERENCES users(id) ON DELETE SET NULL,
    expires_at          TIMESTAMPTZ NOT NULL,
    metadata            JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    approved_at         TIMESTAMPTZ,
    rejected_at         TIMESTAMPTZ,
    cancelled_at        TIMESTAMPTZ,
    completed_at        TIMESTAMPTZ,
    CONSTRAINT external_actions_status_chk CHECK (
        status IN (
            'draft',
            'pending_approval',
            'approved',
            'rejected',
            'dry_run_ready',
            'executing',
            'succeeded',
            'failed',
            'cancelled',
            'expired'
        )
    ),
    CONSTRAINT external_actions_adapter_chk CHECK (adapter_name <> ''),
    CONSTRAINT external_actions_idempotency_chk CHECK (idempotency_key <> '')
);

CREATE UNIQUE INDEX IF NOT EXISTS external_actions_workspace_idempotency_idx
    ON external_actions(workspace_id, idempotency_key);

CREATE INDEX IF NOT EXISTS external_actions_workspace_status_idx
    ON external_actions(workspace_id, status, created_at DESC);

CREATE INDEX IF NOT EXISTS external_actions_source_idx
    ON external_actions(workspace_id, source_type, source_id, created_at DESC);

CREATE TABLE IF NOT EXISTS external_action_events (
    id              BIGSERIAL PRIMARY KEY,
    tenant_id       UUID REFERENCES tenants(id) ON DELETE CASCADE,
    workspace_id    UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    action_id       UUID NOT NULL REFERENCES external_actions(id) ON DELETE CASCADE,
    event_type      TEXT NOT NULL,
    status          TEXT NOT NULL,
    actor_id        BIGINT REFERENCES users(id) ON DELETE SET NULL,
    actor_email     TEXT,
    metadata        JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT external_action_events_type_chk CHECK (
        event_type IN (
            'proposed',
            'dry_run_executed',
            'approved',
            'rejected',
            'execute_started',
            'execute_succeeded',
            'execute_failed',
            'cancelled',
            'expired',
            'blocked'
        )
    )
);

CREATE INDEX IF NOT EXISTS external_action_events_action_idx
    ON external_action_events(workspace_id, action_id, created_at DESC);

CREATE TABLE IF NOT EXISTS external_action_idempotency_keys (
    id              BIGSERIAL PRIMARY KEY,
    tenant_id       UUID REFERENCES tenants(id) ON DELETE CASCADE,
    workspace_id    UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    action_id       UUID REFERENCES external_actions(id) ON DELETE CASCADE,
    operation       TEXT NOT NULL,
    idempotency_key TEXT NOT NULL,
    response        JSONB NOT NULL DEFAULT '{}'::jsonb,
    status          TEXT NOT NULL DEFAULT 'completed',
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT external_action_idempotency_operation_chk CHECK (
        operation IN ('propose', 'dry_run', 'approve', 'reject', 'execute', 'cancel')
    ),
    CONSTRAINT external_action_idempotency_key_chk CHECK (idempotency_key <> '')
);

CREATE UNIQUE INDEX IF NOT EXISTS external_action_idempotency_workspace_key_idx
    ON external_action_idempotency_keys(workspace_id, idempotency_key);

CREATE INDEX IF NOT EXISTS external_action_idempotency_action_operation_idx
    ON external_action_idempotency_keys(workspace_id, action_id, operation);

CREATE OR REPLACE FUNCTION prevent_external_action_event_mutation()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    RAISE EXCEPTION 'external_action_events is append-only';
END;
$$;

DROP TRIGGER IF EXISTS external_action_events_append_only ON external_action_events;
CREATE TRIGGER external_action_events_append_only
    BEFORE UPDATE OR DELETE ON external_action_events
    FOR EACH ROW EXECUTE FUNCTION prevent_external_action_event_mutation();

DO $$
DECLARE
    owner_roles text;
    tbl text;
BEGIN
    SELECT string_agg(quote_ident(rolname), ', ')
      INTO owner_roles
      FROM pg_roles
     WHERE rolname = ANY(ARRAY['omega_console', 'omega_refinement']);

    IF owner_roles IS NULL THEN
        RETURN;
    END IF;

    FOREACH tbl IN ARRAY ARRAY[
        'external_actions',
        'external_action_events',
        'external_action_idempotency_keys'
    ]
    LOOP
        EXECUTE format('ALTER TABLE public.%I ENABLE ROW LEVEL SECURITY', tbl);
        EXECUTE format('ALTER TABLE public.%I FORCE ROW LEVEL SECURITY', tbl);
        EXECUTE format('DROP POLICY IF EXISTS console_refinement_scope_rls ON public.%I', tbl);
        EXECUTE format(
            'CREATE POLICY console_refinement_scope_rls ON public.%I
               FOR ALL
               TO %s
               USING (omega_rls_workspace_matches(tenant_id, workspace_id))
               WITH CHECK (omega_rls_workspace_matches(tenant_id, workspace_id))',
            tbl,
            owner_roles
        );
    END LOOP;
END $$;

GRANT SELECT, INSERT, UPDATE ON external_actions TO omega_console;
GRANT SELECT, INSERT ON external_action_events TO omega_console;
GRANT SELECT, INSERT, UPDATE ON external_action_idempotency_keys TO omega_console;
GRANT USAGE, SELECT ON SEQUENCE external_action_events_id_seq TO omega_console;
GRANT USAGE, SELECT ON SEQUENCE external_action_idempotency_keys_id_seq TO omega_console;

INSERT INTO schema_migrations (filename, applied_at)
VALUES ('99t_external_action_framework.sql', NOW())
ON CONFLICT (filename) DO NOTHING;
