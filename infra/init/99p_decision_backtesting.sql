-- Decision Intelligence backtesting and calibration evidence.
--
-- Backtest runs/results are scoped by tenant/workspace and intentionally keep
-- historical replay labels separate from linked human/operational outcomes.
-- They do not rewrite the immutable decision_intelligence_snapshots payload.

CREATE TABLE IF NOT EXISTS backtest_runs (
    id                           BIGSERIAL PRIMARY KEY,
    tenant_id                    UUID REFERENCES tenants(id) ON DELETE CASCADE,
    workspace_id                 UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    run_ref                      TEXT NOT NULL,
    run_mode                     TEXT NOT NULL,
    source_system                TEXT NOT NULL,
    source_dataset               TEXT,
    metric                       TEXT,
    method                       TEXT,
    started_at                   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    completed_at                 TIMESTAMPTZ,
    status                       TEXT NOT NULL DEFAULT 'running',
    periods_evaluated            INTEGER NOT NULL DEFAULT 0,
    labels_available             INTEGER NOT NULL DEFAULT 0,
    labels_required              INTEGER NOT NULL DEFAULT 10,
    insufficient_labeled_data    BOOLEAN NOT NULL DEFAULT TRUE,
    config                       JSONB NOT NULL DEFAULT '{}'::jsonb,
    summary                      JSONB NOT NULL DEFAULT '{}'::jsonb,
    actor_user_id                BIGINT REFERENCES users(id) ON DELETE SET NULL,
    app_version                  TEXT,
    deploy_ref                   TEXT,
    created_at                   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at                   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT backtest_runs_mode_chk
      CHECK (run_mode IN ('historical_replay', 'outcome_linked', 'fixture_validation')),
    CONSTRAINT backtest_runs_status_chk
      CHECK (status IN ('running', 'ok', 'insufficient_labeled_data', 'dataset_unavailable', 'failed'))
);

CREATE UNIQUE INDEX IF NOT EXISTS backtest_runs_workspace_ref_idx
    ON backtest_runs(workspace_id, run_ref);

CREATE UNIQUE INDEX IF NOT EXISTS backtest_runs_workspace_id_idx
    ON backtest_runs(workspace_id, id);

CREATE INDEX IF NOT EXISTS backtest_runs_workspace_latest_idx
    ON backtest_runs(workspace_id, source_system, metric, completed_at DESC, id DESC);

CREATE INDEX IF NOT EXISTS backtest_runs_workspace_status_idx
    ON backtest_runs(workspace_id, status, started_at DESC);

CREATE TABLE IF NOT EXISTS backtest_results (
    id                            BIGSERIAL PRIMARY KEY,
    tenant_id                     UUID REFERENCES tenants(id) ON DELETE CASCADE,
    workspace_id                  UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    backtest_run_id               BIGINT NOT NULL,
    snapshot_id                   BIGINT REFERENCES decision_intelligence_snapshots(id) ON DELETE SET NULL,
    signal_id                     TEXT,
    control_room_item_id          TEXT,
    source_system                 TEXT NOT NULL,
    source_dataset                TEXT,
    gold_table                    TEXT,
    metric                        TEXT,
    entity_key                    TEXT,
    period_key                    TEXT,
    as_of_period                  TEXT,
    method                        TEXT,
    anomaly_probability           NUMERIC,
    predicted_label               BOOLEAN,
    actual_label                  BOOLEAN,
    label_source                  TEXT NOT NULL DEFAULT 'unavailable',
    recommended_decision          TEXT,
    uncertainty_level             TEXT,
    data_quality_status           TEXT,
    expected_impact_value         NUMERIC,
    measured_impact               NUMERIC,
    error_abs                     NUMERIC,
    is_true_positive              BOOLEAN,
    is_false_positive             BOOLEAN,
    is_true_negative              BOOLEAN,
    is_false_negative             BOOLEAN,
    result                        JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at                    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    FOREIGN KEY (workspace_id, backtest_run_id)
      REFERENCES backtest_runs(workspace_id, id)
      ON DELETE CASCADE,
    CONSTRAINT backtest_results_label_source_chk
      CHECK (label_source IN ('outcome', 'historical_rule', 'fixture', 'unavailable'))
);

CREATE INDEX IF NOT EXISTS backtest_results_run_idx
    ON backtest_results(workspace_id, backtest_run_id, id);

CREATE INDEX IF NOT EXISTS backtest_results_metric_period_idx
    ON backtest_results(workspace_id, source_system, metric, period_key);

CREATE INDEX IF NOT EXISTS backtest_results_snapshot_idx
    ON backtest_results(workspace_id, snapshot_id)
    WHERE snapshot_id IS NOT NULL;

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

    FOREACH tbl IN ARRAY ARRAY['backtest_runs', 'backtest_results']
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

GRANT SELECT, INSERT, UPDATE ON backtest_runs TO omega_console;
GRANT SELECT, INSERT ON backtest_results TO omega_console;
GRANT USAGE, SELECT ON SEQUENCE backtest_runs_id_seq TO omega_console;
GRANT USAGE, SELECT ON SEQUENCE backtest_results_id_seq TO omega_console;

INSERT INTO schema_migrations (filename, applied_at)
VALUES ('99p_decision_backtesting.sql', NOW())
ON CONFLICT (filename) DO NOTHING;
