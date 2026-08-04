-- Bayesian calibration loop for Decision Intelligence.
--
-- Console sets app.tenant_id/app.workspace_id through scoped_db_for_user before
-- touching these tables. Policies fail closed when the workspace GUC is absent.

CREATE TABLE IF NOT EXISTS calibration_observations (
    id                       BIGSERIAL PRIMARY KEY,
    observation_id           TEXT NOT NULL,
    idempotency_key           TEXT NOT NULL,
    evidence_digest           TEXT NOT NULL,
    tenant_id                UUID REFERENCES tenants(id) ON DELETE CASCADE,
    workspace_id             UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    source_type              TEXT NOT NULL
      CHECK (source_type IN (
        'monte_carlo_simulation',
        'decision_option',
        'prediction_outcome',
        'backtest_case',
        'manual_fixture'
      )),
    source_id                TEXT NOT NULL,
    predicted_metric         TEXT NOT NULL,
    predicted_value          NUMERIC,
    predicted_interval       JSONB NOT NULL DEFAULT '{}'::jsonb,
    predicted_probability    NUMERIC
      CHECK (predicted_probability IS NULL OR predicted_probability BETWEEN 0 AND 1),
    actual_value             NUMERIC,
    actual_status            TEXT NOT NULL
      CHECK (actual_status IN ('hit', 'miss', 'partial', 'unknown')),
    observed_at              TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    horizon_days             INTEGER NOT NULL DEFAULT 30
      CHECK (horizon_days BETWEEN 1 AND 3650),
    model_version            TEXT NOT NULL,
    calibration_group        TEXT NOT NULL,
    prior                    JSONB NOT NULL DEFAULT '{}'::jsonb,
    posterior                JSONB NOT NULL DEFAULT '{}'::jsonb,
    metrics                  JSONB NOT NULL DEFAULT '{}'::jsonb,
    evidence_refs            JSONB NOT NULL DEFAULT '[]'::jsonb,
    explanation              TEXT,
    reproducibility_hash     TEXT NOT NULL,
    created_by               BIGINT REFERENCES users(id) ON DELETE SET NULL,
    created_at               TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at               TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (workspace_id, observation_id),
    UNIQUE (workspace_id, idempotency_key)
);

CREATE TABLE IF NOT EXISTS calibration_states (
    id                       BIGSERIAL PRIMARY KEY,
    state_id                 TEXT NOT NULL,
    tenant_id                UUID REFERENCES tenants(id) ON DELETE CASCADE,
    workspace_id             UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    calibration_group        TEXT NOT NULL,
    model_version            TEXT NOT NULL,
    prior                    JSONB NOT NULL DEFAULT '{}'::jsonb,
    posterior                JSONB NOT NULL DEFAULT '{}'::jsonb,
    metrics                  JSONB NOT NULL DEFAULT '{}'::jsonb,
    sample_count             INTEGER NOT NULL DEFAULT 0,
    hit_count                INTEGER NOT NULL DEFAULT 0,
    miss_count               INTEGER NOT NULL DEFAULT 0,
    partial_count            INTEGER NOT NULL DEFAULT 0,
    unknown_count            INTEGER NOT NULL DEFAULT 0,
    brier_score              NUMERIC,
    mae                      NUMERIC,
    rmse                     NUMERIC,
    coverage_p10_p90         NUMERIC,
    calibration_error        NUMERIC,
    confidence_score         NUMERIC,
    last_observed_at         TIMESTAMPTZ,
    reproducibility_hash     TEXT NOT NULL,
    created_at               TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at               TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (workspace_id, calibration_group, model_version)
);

CREATE INDEX IF NOT EXISTS calibration_observations_workspace_source_idx
    ON calibration_observations(workspace_id, source_type, source_id, observed_at DESC);

CREATE INDEX IF NOT EXISTS calibration_observations_workspace_group_idx
    ON calibration_observations(workspace_id, calibration_group, model_version, observed_at DESC);

CREATE INDEX IF NOT EXISTS calibration_states_workspace_group_idx
    ON calibration_states(workspace_id, calibration_group, model_version, updated_at DESC);

ALTER TABLE calibration_observations ENABLE ROW LEVEL SECURITY;
ALTER TABLE calibration_observations FORCE ROW LEVEL SECURITY;
ALTER TABLE calibration_states ENABLE ROW LEVEL SECURITY;
ALTER TABLE calibration_states FORCE ROW LEVEL SECURITY;

ALTER ROLE omega_console NOBYPASSRLS;

DROP POLICY IF EXISTS calibration_observations_console_scope_rls
    ON calibration_observations;
DROP POLICY IF EXISTS calibration_states_console_scope_rls
    ON calibration_states;

CREATE POLICY calibration_observations_console_scope_rls
    ON calibration_observations
    FOR ALL
    TO omega_console
    USING (
        workspace_id::text = NULLIF(current_setting('app.workspace_id', true), '')
        AND (
            tenant_id IS NULL
            OR tenant_id::text = NULLIF(current_setting('app.tenant_id', true), '')
        )
    )
    WITH CHECK (
        workspace_id::text = NULLIF(current_setting('app.workspace_id', true), '')
        AND (
            tenant_id IS NULL
            OR tenant_id::text = NULLIF(current_setting('app.tenant_id', true), '')
        )
    );

CREATE POLICY calibration_states_console_scope_rls
    ON calibration_states
    FOR ALL
    TO omega_console
    USING (
        workspace_id::text = NULLIF(current_setting('app.workspace_id', true), '')
        AND (
            tenant_id IS NULL
            OR tenant_id::text = NULLIF(current_setting('app.tenant_id', true), '')
        )
    )
    WITH CHECK (
        workspace_id::text = NULLIF(current_setting('app.workspace_id', true), '')
        AND (
            tenant_id IS NULL
            OR tenant_id::text = NULLIF(current_setting('app.tenant_id', true), '')
        )
    );

REVOKE UPDATE, DELETE ON calibration_observations FROM omega_console;
GRANT SELECT, INSERT ON calibration_observations TO omega_console;
GRANT SELECT, INSERT, UPDATE ON calibration_states TO omega_console;
GRANT USAGE, SELECT ON SEQUENCE calibration_observations_id_seq TO omega_console;
GRANT USAGE, SELECT ON SEQUENCE calibration_states_id_seq TO omega_console;

INSERT INTO schema_migrations (filename, applied_at)
VALUES ('99r_bayesian_calibration.sql', NOW())
ON CONFLICT (filename) DO NOTHING;
