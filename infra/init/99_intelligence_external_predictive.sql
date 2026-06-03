-- v1.45.1: predictive signals and workspace-scoped external intelligence.

ALTER TABLE intelligence_signals
    ADD COLUMN IF NOT EXISTS prediction_horizon_days INTEGER;

ALTER TABLE intelligence_signals
    ADD COLUMN IF NOT EXISTS predicted_value NUMERIC(18,4);

ALTER TABLE intelligence_signals
    ADD COLUMN IF NOT EXISTS prediction_method TEXT;

ALTER TABLE intelligence_signals
    ADD COLUMN IF NOT EXISTS signal_subtype TEXT NOT NULL DEFAULT 'observed';

CREATE INDEX IF NOT EXISTS intelligence_signals_workspace_subtype_idx
    ON intelligence_signals(workspace_id, signal_subtype, prediction_horizon_days, updated_at DESC);

CREATE TABLE IF NOT EXISTS external_intelligence_sources (
    id            BIGSERIAL PRIMARY KEY,
    tenant_id     UUID REFERENCES tenants(id) ON DELETE CASCADE,
    workspace_id  UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    source_id     TEXT NOT NULL,
    source_type   TEXT NOT NULL,
    cartridge_id  TEXT,
    metric        TEXT,
    enabled       BOOLEAN NOT NULL DEFAULT TRUE,
    config        JSONB NOT NULL DEFAULT '{}'::jsonb,
    ttl_seconds   INTEGER NOT NULL DEFAULT 86400,
    last_run_at   TIMESTAMPTZ,
    last_status   TEXT,
    metadata      JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (workspace_id, source_id, cartridge_id, metric)
);

CREATE INDEX IF NOT EXISTS external_intelligence_sources_workspace_idx
    ON external_intelligence_sources(workspace_id, enabled, source_type, updated_at DESC);

CREATE TABLE IF NOT EXISTS external_evidence_cache (
    id            BIGSERIAL PRIMARY KEY,
    tenant_id     UUID REFERENCES tenants(id) ON DELETE CASCADE,
    workspace_id  UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    source_id     TEXT NOT NULL,
    source_type   TEXT NOT NULL,
    entity_kind   TEXT NOT NULL DEFAULT '*',
    entity_id     TEXT NOT NULL DEFAULT '*',
    period_key    TEXT NOT NULL DEFAULT '*',
    data          JSONB NOT NULL DEFAULT '{}'::jsonb,
    strength      NUMERIC(5,2) NOT NULL DEFAULT 0.50,
    expires_at    TIMESTAMPTZ NOT NULL,
    metadata      JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (workspace_id, source_id, entity_kind, entity_id, period_key)
);

CREATE INDEX IF NOT EXISTS external_evidence_cache_workspace_lookup_idx
    ON external_evidence_cache(workspace_id, source_id, entity_kind, entity_id, period_key, expires_at DESC);

INSERT INTO schema_migrations (filename, applied_at)
VALUES ('99_intelligence_external_predictive.sql', NOW())
ON CONFLICT (filename) DO NOTHING;
