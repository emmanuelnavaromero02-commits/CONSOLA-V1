-- OMEGA operational intelligence engine: signals, baselines, evidence,
-- hypotheses, scored options and outcome learning.

CREATE TABLE IF NOT EXISTS metric_baselines (
    id              BIGSERIAL PRIMARY KEY,
    tenant_id       UUID REFERENCES tenants(id) ON DELETE CASCADE,
    workspace_id    UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    cartridge_id    TEXT NOT NULL,
    dataset         TEXT NOT NULL,
    metric          TEXT NOT NULL,
    entity_kind     TEXT NOT NULL DEFAULT 'entity',
    entity_id       TEXT NOT NULL,
    entity_label    TEXT NOT NULL,
    dimensions      JSONB NOT NULL DEFAULT '{}'::jsonb,
    period_key      TEXT NOT NULL,
    method          TEXT NOT NULL,
    actual_value    NUMERIC(18,4),
    expected_value  NUMERIC(18,4),
    sample_count    INTEGER NOT NULL DEFAULT 0,
    window_days     INTEGER NOT NULL DEFAULT 0,
    confidence      NUMERIC(5,2) NOT NULL DEFAULT 0.50,
    metadata        JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (workspace_id, cartridge_id, dataset, metric, entity_id, period_key, method)
);

CREATE INDEX IF NOT EXISTS metric_baselines_workspace_metric_idx
    ON metric_baselines(workspace_id, cartridge_id, metric, updated_at DESC);

CREATE TABLE IF NOT EXISTS intelligence_signals (
    signal_id       TEXT NOT NULL,
    tenant_id       UUID REFERENCES tenants(id) ON DELETE CASCADE,
    workspace_id    UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    cartridge_id    TEXT NOT NULL,
    dataset         TEXT NOT NULL,
    domain          TEXT NOT NULL DEFAULT 'Operacion',
    entity_kind     TEXT NOT NULL DEFAULT 'entity',
    entity_id       TEXT NOT NULL,
    entity_label    TEXT NOT NULL,
    metric          TEXT NOT NULL,
    period_key      TEXT NOT NULL,
    actual_value    NUMERIC(18,4),
    expected_value  NUMERIC(18,4),
    deviation_value NUMERIC(18,4),
    deviation_pct   NUMERIC(9,4),
    severity        TEXT NOT NULL DEFAULT 'medium'
      CHECK (severity IN ('critical', 'high', 'medium', 'low')),
    signal_type     TEXT NOT NULL DEFAULT 'risk'
      CHECK (signal_type IN ('risk', 'opportunity', 'watch')),
    status          TEXT NOT NULL DEFAULT 'open'
      CHECK (status IN ('open', 'in_review', 'decision_created', 'approved', 'dismissed', 'resolved')),
    baseline_id     BIGINT REFERENCES metric_baselines(id) ON DELETE SET NULL,
    confidence      NUMERIC(5,2) NOT NULL DEFAULT 0.50,
    summary         TEXT NOT NULL,
    metadata        JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (workspace_id, signal_id)
);

CREATE INDEX IF NOT EXISTS intelligence_signals_workspace_status_idx
    ON intelligence_signals(workspace_id, status, severity, updated_at DESC);

CREATE INDEX IF NOT EXISTS intelligence_signals_workspace_metric_idx
    ON intelligence_signals(workspace_id, cartridge_id, metric, period_key DESC);

CREATE TABLE IF NOT EXISTS evidence_packs (
    id              BIGSERIAL PRIMARY KEY,
    tenant_id       UUID REFERENCES tenants(id) ON DELETE CASCADE,
    workspace_id    UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    signal_id       TEXT NOT NULL,
    summary         TEXT NOT NULL,
    confidence      NUMERIC(5,2) NOT NULL DEFAULT 0.50,
    metadata        JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS evidence_packs_signal_idx
    ON evidence_packs(workspace_id, signal_id, created_at DESC);

CREATE TABLE IF NOT EXISTS evidence_items (
    id                BIGSERIAL PRIMARY KEY,
    tenant_id         UUID REFERENCES tenants(id) ON DELETE CASCADE,
    workspace_id      UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    evidence_pack_id  BIGINT NOT NULL REFERENCES evidence_packs(id) ON DELETE CASCADE,
    source_type       TEXT NOT NULL,
    source_ref        TEXT NOT NULL,
    query_text        TEXT,
    data              JSONB NOT NULL DEFAULT '{}'::jsonb,
    supports_hypothesis TEXT,
    strength          NUMERIC(5,2) NOT NULL DEFAULT 0.50,
    metadata          JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS evidence_items_pack_idx
    ON evidence_items(evidence_pack_id, strength DESC);

CREATE INDEX IF NOT EXISTS evidence_items_workspace_pack_idx
    ON evidence_items(workspace_id, evidence_pack_id, strength DESC);

CREATE TABLE IF NOT EXISTS hypotheses (
    id               BIGSERIAL PRIMARY KEY,
    tenant_id        UUID REFERENCES tenants(id) ON DELETE CASCADE,
    workspace_id     UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    signal_id        TEXT NOT NULL,
    hypothesis_key   TEXT NOT NULL,
    title            TEXT NOT NULL,
    rationale        TEXT NOT NULL,
    confidence       NUMERIC(5,2) NOT NULL DEFAULT 0.50,
    evidence_pack_id BIGINT REFERENCES evidence_packs(id) ON DELETE SET NULL,
    metadata         JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (workspace_id, signal_id, hypothesis_key)
);

CREATE INDEX IF NOT EXISTS hypotheses_signal_idx
    ON hypotheses(workspace_id, signal_id, confidence DESC);

CREATE TABLE IF NOT EXISTS decision_options (
    id               BIGSERIAL PRIMARY KEY,
    tenant_id        UUID REFERENCES tenants(id) ON DELETE CASCADE,
    workspace_id     UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    signal_id        TEXT NOT NULL,
    option_id        TEXT NOT NULL,
    label            TEXT NOT NULL,
    action_kind      TEXT NOT NULL,
    impact_expected  NUMERIC(18,4) NOT NULL DEFAULT 0,
    confidence       NUMERIC(5,2) NOT NULL DEFAULT 0.50,
    cost             NUMERIC(18,4) NOT NULL DEFAULT 0,
    risk             NUMERIC(18,4) NOT NULL DEFAULT 0,
    time_cost        NUMERIC(18,4) NOT NULL DEFAULT 0,
    score            NUMERIC(18,4) NOT NULL DEFAULT 0,
    selected         BOOLEAN NOT NULL DEFAULT FALSE,
    metadata         JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (workspace_id, signal_id, option_id)
);

CREATE INDEX IF NOT EXISTS decision_options_signal_idx
    ON decision_options(workspace_id, signal_id, score DESC);

CREATE TABLE IF NOT EXISTS prediction_outcomes (
    id               BIGSERIAL PRIMARY KEY,
    tenant_id        UUID REFERENCES tenants(id) ON DELETE CASCADE,
    workspace_id     UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    signal_id        TEXT NOT NULL,
    option_id        TEXT,
    action_taken     TEXT NOT NULL,
    predicted_value  NUMERIC(18,4),
    actual_value     NUMERIC(18,4),
    prediction_error NUMERIC(18,4),
    outcome_summary  TEXT NOT NULL,
    learned_rule     TEXT,
    metadata         JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS prediction_outcomes_signal_idx
    ON prediction_outcomes(workspace_id, signal_id, created_at DESC);

INSERT INTO schema_migrations (filename, applied_at)
VALUES ('98_intelligence_engine.sql', NOW())
ON CONFLICT (filename) DO NOTHING;
