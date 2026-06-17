-- Prompt 19A: automatic decision orchestrator.
--
-- The orchestrator classifies operational signals and records an advisory
-- plan. It does not execute optimization engines or external write-back.

CREATE EXTENSION IF NOT EXISTS pgcrypto;

CREATE TABLE IF NOT EXISTS decision_orchestration_runs (
    id                        BIGSERIAL PRIMARY KEY,
    orchestration_id          TEXT NOT NULL,
    tenant_id                 UUID REFERENCES tenants(id) ON DELETE CASCADE,
    workspace_id              UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    source_type               TEXT NOT NULL,
    source_id                 TEXT NOT NULL,
    problem_type              TEXT NOT NULL,
    secondary_problem_types   JSONB NOT NULL DEFAULT '[]'::jsonb,
    confidence                NUMERIC(5,4) NOT NULL DEFAULT 0.0000
      CHECK (confidence BETWEEN 0 AND 1),
    recommended_engines       JSONB NOT NULL DEFAULT '[]'::jsonb,
    candidate_engines         JSONB NOT NULL DEFAULT '[]'::jsonb,
    engine_plan               JSONB NOT NULL DEFAULT '{}'::jsonb,
    decision_plan             JSONB NOT NULL DEFAULT '{}'::jsonb,
    action_recommended        BOOLEAN NOT NULL DEFAULT FALSE,
    external_action_id        UUID REFERENCES external_actions(id) ON DELETE SET NULL,
    reasoning_summary         TEXT NOT NULL,
    safety_notes              JSONB NOT NULL DEFAULT '[]'::jsonb,
    missing_data              JSONB NOT NULL DEFAULT '[]'::jsonb,
    created_by                BIGINT REFERENCES users(id) ON DELETE SET NULL,
    created_at                TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at                TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT decision_orchestration_runs_source_type_chk CHECK (
        source_type IN (
            'control_room_item',
            'agent_alert',
            'intelligence_signal',
            'monte_carlo_simulation',
            'calibration_observation',
            'manual_fixture'
        )
    ),
    CONSTRAINT decision_orchestration_runs_problem_type_chk CHECK (
        problem_type IN (
            'risk_forecast',
            'resource_allocation',
            'budget_optimization',
            'capacity_planning',
            'scheduling',
            'temporal_control',
            'multi_actor_strategy',
            'simple_action',
            'data_quality',
            'insufficient_data',
            'unknown'
        )
    ),
    UNIQUE (workspace_id, orchestration_id)
);

CREATE INDEX IF NOT EXISTS decision_orchestration_runs_workspace_created_idx
    ON decision_orchestration_runs(workspace_id, created_at DESC);

CREATE INDEX IF NOT EXISTS decision_orchestration_runs_source_idx
    ON decision_orchestration_runs(workspace_id, source_type, source_id, created_at DESC);

ALTER TABLE decision_orchestration_runs ENABLE ROW LEVEL SECURITY;
ALTER TABLE decision_orchestration_runs FORCE ROW LEVEL SECURITY;

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

    DROP POLICY IF EXISTS decision_orchestration_runs_console_scope_rls
        ON decision_orchestration_runs;

    EXECUTE format(
        'CREATE POLICY decision_orchestration_runs_console_scope_rls
           ON decision_orchestration_runs
           FOR ALL
           TO %s
           USING (omega_rls_workspace_matches(tenant_id, workspace_id))
           WITH CHECK (omega_rls_workspace_matches(tenant_id, workspace_id))',
        owner_roles
    );
END $$;

GRANT SELECT, INSERT, UPDATE ON decision_orchestration_runs TO omega_console;
GRANT USAGE, SELECT ON SEQUENCE decision_orchestration_runs_id_seq TO omega_console;

INSERT INTO schema_migrations (filename, applied_at)
VALUES ('99u_decision_orchestrator.sql', NOW())
ON CONFLICT (filename) DO NOTHING;
