-- Monte Carlo sensitivity analysis for Decision Intelligence.
--
-- Console sets app.tenant_id/app.workspace_id before touching this table.
-- Policies fail closed when the workspace GUC is absent.

CREATE TABLE IF NOT EXISTS monte_carlo_simulations (
    id                         BIGSERIAL PRIMARY KEY,
    simulation_id              TEXT NOT NULL,
    tenant_id                  UUID REFERENCES tenants(id) ON DELETE CASCADE,
    workspace_id               UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    source_type                TEXT NOT NULL
      CHECK (source_type IN ('signal', 'decision_option', 'manual_fixture', 'backtest_case', 'wisdom_bit')),
    source_id                  TEXT NOT NULL,
    horizon_days               INTEGER NOT NULL DEFAULT 30
      CHECK (horizon_days BETWEEN 1 AND 365),
    iterations                 INTEGER NOT NULL DEFAULT 1000
      CHECK (iterations BETWEEN 1 AND 10000),
    seed                       BIGINT NOT NULL DEFAULT 0,
    model_version              TEXT NOT NULL,
    input_variables            JSONB NOT NULL DEFAULT '{}'::jsonb,
    assumptions                JSONB NOT NULL DEFAULT '{}'::jsonb,
    output_metric              TEXT NOT NULL
      CHECK (output_metric IN ('net_value', 'delta', 'cost', 'delay_days')),
    breach_threshold           NUMERIC,
    breach_direction           TEXT
      CHECK (breach_direction IS NULL OR breach_direction IN ('below', 'above')),
    distribution_summary       JSONB NOT NULL DEFAULT '{}'::jsonb,
    sensitivity                JSONB NOT NULL DEFAULT '[]'::jsonb,
    option_comparison          JSONB NOT NULL DEFAULT '{}'::jsonb,
    evidence_refs              JSONB NOT NULL DEFAULT '[]'::jsonb,
    reproducibility_hash       TEXT NOT NULL,
    created_by                 BIGINT REFERENCES users(id) ON DELETE SET NULL,
    created_at                 TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at                 TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (workspace_id, simulation_id)
);

CREATE INDEX IF NOT EXISTS monte_carlo_simulations_workspace_source_idx
    ON monte_carlo_simulations(workspace_id, source_type, source_id, updated_at DESC);

CREATE INDEX IF NOT EXISTS monte_carlo_simulations_workspace_created_idx
    ON monte_carlo_simulations(workspace_id, created_at DESC);

ALTER TABLE monte_carlo_simulations ENABLE ROW LEVEL SECURITY;
ALTER TABLE monte_carlo_simulations FORCE ROW LEVEL SECURITY;

ALTER ROLE omega_console NOBYPASSRLS;

DROP POLICY IF EXISTS monte_carlo_simulations_console_scope_rls
    ON monte_carlo_simulations;

CREATE POLICY monte_carlo_simulations_console_scope_rls
    ON monte_carlo_simulations
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

GRANT SELECT, INSERT, UPDATE ON monte_carlo_simulations TO omega_console;
GRANT USAGE, SELECT ON SEQUENCE monte_carlo_simulations_id_seq TO omega_console;

INSERT INTO schema_migrations (filename, applied_at)
VALUES ('99q_monte_carlo_simulations.sql', NOW())
ON CONFLICT (filename) DO NOTHING;
