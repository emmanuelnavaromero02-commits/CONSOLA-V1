-- Prompt 19B: safe internal engine execution for the decision orchestrator.
--
-- This table stores tenant-scoped execution evidence for allowlisted internal
-- engines. It does not enable external write-back or candidate engines.

CREATE EXTENSION IF NOT EXISTS pgcrypto;

CREATE TABLE IF NOT EXISTS decision_orchestration_executions (
    id                   BIGSERIAL PRIMARY KEY,
    tenant_id            UUID REFERENCES tenants(id) ON DELETE CASCADE,
    workspace_id         UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    orchestration_id     TEXT NOT NULL,
    engine_name          TEXT NOT NULL,
    execution_status     TEXT NOT NULL,
    input_hash           TEXT NOT NULL,
    output_hash          TEXT,
    input_summary        JSONB NOT NULL DEFAULT '{}'::jsonb,
    result_summary       JSONB NOT NULL DEFAULT '{}'::jsonb,
    evidence_refs        JSONB NOT NULL DEFAULT '[]'::jsonb,
    error_code           TEXT,
    error_message        TEXT,
    started_at           TIMESTAMPTZ,
    finished_at          TIMESTAMPTZ,
    created_by           BIGINT REFERENCES users(id) ON DELETE SET NULL,
    created_at           TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at           TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT decision_orchestration_executions_status_chk CHECK (
        execution_status IN (
            'pending',
            'running',
            'succeeded',
            'skipped',
            'failed',
            'candidate_only'
        )
    ),
    CONSTRAINT decision_orchestration_executions_run_fk
      FOREIGN KEY (workspace_id, orchestration_id)
      REFERENCES decision_orchestration_runs(workspace_id, orchestration_id)
      ON DELETE CASCADE,
    UNIQUE (workspace_id, orchestration_id, engine_name, input_hash)
);

CREATE INDEX IF NOT EXISTS decision_orchestration_executions_run_idx
    ON decision_orchestration_executions(workspace_id, orchestration_id, created_at DESC);

CREATE INDEX IF NOT EXISTS decision_orchestration_executions_engine_idx
    ON decision_orchestration_executions(workspace_id, engine_name, execution_status, created_at DESC);

ALTER TABLE decision_orchestration_executions ENABLE ROW LEVEL SECURITY;
ALTER TABLE decision_orchestration_executions FORCE ROW LEVEL SECURITY;

DO $$
DECLARE
    owner_roles text;
BEGIN
    SELECT string_agg(quote_ident(rolname), ', ')
      INTO owner_roles
      FROM pg_roles
     WHERE rolname = ANY(ARRAY['omega_console', 'omega_refinement']);

    IF owner_roles IS NULL THEN
        RETURN;
    END IF;

    DROP POLICY IF EXISTS decision_orchestration_executions_console_scope_rls
        ON decision_orchestration_executions;

    EXECUTE format(
        'CREATE POLICY decision_orchestration_executions_console_scope_rls
           ON decision_orchestration_executions
           FOR ALL
           TO %s
           USING (omega_rls_workspace_matches(tenant_id, workspace_id))
           WITH CHECK (omega_rls_workspace_matches(tenant_id, workspace_id))',
        owner_roles
    );
END $$;

GRANT SELECT, INSERT, UPDATE ON decision_orchestration_executions TO omega_console;
GRANT USAGE, SELECT ON SEQUENCE decision_orchestration_executions_id_seq TO omega_console;

INSERT INTO schema_migrations (filename, applied_at)
VALUES ('99v_decision_orchestrator_executions.sql', NOW())
ON CONFLICT (filename) DO NOTHING;
