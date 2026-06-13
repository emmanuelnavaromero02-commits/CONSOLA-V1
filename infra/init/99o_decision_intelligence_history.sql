-- Decision Intelligence run history and immutable recommendation snapshots.
--
-- These tables make Decision Intelligence auditable without pretending that
-- v0 probabilities are already calibrated. Outcomes link back to snapshots
-- later; the original recommendation JSON is never overwritten.

CREATE TABLE IF NOT EXISTS intelligence_runs (
    id                           BIGSERIAL PRIMARY KEY,
    run_ref                      TEXT NOT NULL,
    tenant_id                    UUID REFERENCES tenants(id) ON DELETE CASCADE,
    workspace_id                 UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    source_system                TEXT,
    run_mode                     TEXT NOT NULL DEFAULT 'manual',
    status                       TEXT NOT NULL DEFAULT 'running',
    datasets_evaluated           JSONB NOT NULL DEFAULT '[]'::jsonb,
    signals_generated            INTEGER NOT NULL DEFAULT 0,
    signals_skipped              INTEGER NOT NULL DEFAULT 0,
    dataset_unavailable_count    INTEGER NOT NULL DEFAULT 0,
    insufficient_history_count   INTEGER NOT NULL DEFAULT 0,
    errors                       JSONB NOT NULL DEFAULT '[]'::jsonb,
    request                      JSONB NOT NULL DEFAULT '{}'::jsonb,
    metadata                     JSONB NOT NULL DEFAULT '{}'::jsonb,
    app_version                  TEXT,
    deploy_ref                   TEXT,
    owner_user_id                BIGINT REFERENCES users(id) ON DELETE SET NULL,
    started_at                   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    completed_at                 TIMESTAMPTZ,
    created_at                   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at                   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT intelligence_runs_mode_chk
      CHECK (run_mode IN ('manual', 'scheduled', 'backtest', 'smoke')),
    CONSTRAINT intelligence_runs_status_chk
      CHECK (status IN ('running', 'completed', 'failed', 'not_ready'))
);

CREATE UNIQUE INDEX IF NOT EXISTS intelligence_runs_workspace_ref_idx
    ON intelligence_runs(workspace_id, run_ref);

CREATE UNIQUE INDEX IF NOT EXISTS intelligence_runs_workspace_id_idx
    ON intelligence_runs(workspace_id, id);

CREATE INDEX IF NOT EXISTS intelligence_runs_status_idx
    ON intelligence_runs(workspace_id, status, started_at DESC);

CREATE INDEX IF NOT EXISTS intelligence_runs_mode_idx
    ON intelligence_runs(workspace_id, run_mode, started_at DESC);

CREATE TABLE IF NOT EXISTS decision_intelligence_snapshots (
    id                              BIGSERIAL PRIMARY KEY,
    tenant_id                       UUID REFERENCES tenants(id) ON DELETE CASCADE,
    workspace_id                    UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    signal_id                       TEXT NOT NULL,
    control_room_item_id            TEXT,
    evidence_pack_id                BIGINT REFERENCES evidence_packs(id) ON DELETE SET NULL,
    intelligence_run_id             BIGINT,
    run_ref                         TEXT,
    source_system                   TEXT,
    source_dataset                  TEXT,
    gold_table                      TEXT,
    metric                          TEXT,
    entity_kind                     TEXT,
    entity_id                       TEXT,
    entity_key                      TEXT,
    period_key                      TEXT,
    freshness_at                    TEXT,
    decision_intelligence           JSONB NOT NULL DEFAULT '{}'::jsonb,
    recommended_decision            TEXT,
    anomaly_probability             NUMERIC,
    uncertainty_level               TEXT,
    expected_impact_value           NUMERIC,
    expected_impact_currency        TEXT,
    data_quality_status             TEXT,
    method                          TEXT,
    decision_intelligence_version   TEXT NOT NULL DEFAULT 'decision_intelligence_v0',
    model_version                   TEXT NOT NULL DEFAULT 'decision_intelligence_v0',
    outcome_id                      BIGINT REFERENCES prediction_outcomes(id) ON DELETE SET NULL,
    outcome_observed_at             TIMESTAMPTZ,
    outcome_status                  TEXT,
    measured_impact                 NUMERIC,
    calibration_status              TEXT NOT NULL DEFAULT 'pending_outcome',
    owner_user_id                   BIGINT REFERENCES users(id) ON DELETE SET NULL,
    metadata                        JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at                      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at                      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    FOREIGN KEY (workspace_id, signal_id)
      REFERENCES intelligence_signals(workspace_id, signal_id)
      ON DELETE CASCADE,
    FOREIGN KEY (workspace_id, control_room_item_id)
      REFERENCES control_room_items(workspace_id, item_id)
      ON DELETE SET NULL,
    FOREIGN KEY (workspace_id, intelligence_run_id)
      REFERENCES intelligence_runs(workspace_id, id)
      ON DELETE SET NULL
);

CREATE INDEX IF NOT EXISTS decision_intelligence_snapshots_run_idx
    ON decision_intelligence_snapshots(workspace_id, intelligence_run_id, created_at DESC);

CREATE INDEX IF NOT EXISTS decision_intelligence_snapshots_signal_idx
    ON decision_intelligence_snapshots(workspace_id, signal_id, created_at DESC);

CREATE INDEX IF NOT EXISTS decision_intelligence_snapshots_item_idx
    ON decision_intelligence_snapshots(workspace_id, control_room_item_id, created_at DESC)
    WHERE control_room_item_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS decision_intelligence_snapshots_outcome_idx
    ON decision_intelligence_snapshots(workspace_id, outcome_id)
    WHERE outcome_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS decision_intelligence_snapshots_calibration_idx
    ON decision_intelligence_snapshots(workspace_id, method, recommended_decision, data_quality_status, created_at DESC);

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

    FOREACH tbl IN ARRAY ARRAY['intelligence_runs', 'decision_intelligence_snapshots']
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

GRANT SELECT, INSERT, UPDATE ON intelligence_runs TO omega_console;
GRANT SELECT, INSERT, UPDATE ON decision_intelligence_snapshots TO omega_console;
GRANT USAGE, SELECT ON SEQUENCE intelligence_runs_id_seq TO omega_console;
GRANT USAGE, SELECT ON SEQUENCE decision_intelligence_snapshots_id_seq TO omega_console;

INSERT INTO schema_migrations (filename, applied_at)
VALUES ('99o_decision_intelligence_history.sql', NOW())
ON CONFLICT (filename) DO NOTHING;
