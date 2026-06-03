-- Native Postgres RLS for the operational intelligence tables.
--
-- The application sets app.tenant_id/app.workspace_id with SET LOCAL before
-- touching these tables. Policies intentionally fail closed when workspace_id
-- is not present in the DB session.

-- Docker's init glob order can place this file before
-- 99_intelligence_external_predictive.sql on some locales. Keep these table
-- definitions here too so external-intelligence RLS is never skipped.
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

DO $$
DECLARE
    table_name text;
BEGIN
    FOREACH table_name IN ARRAY ARRAY[
        'metric_baselines',
        'intelligence_signals',
        'evidence_packs',
        'evidence_items',
        'hypotheses',
        'decision_options',
        'prediction_outcomes',
        'external_intelligence_sources',
        'external_evidence_cache'
    ]
    LOOP
        EXECUTE format('ALTER TABLE %I ENABLE ROW LEVEL SECURITY', table_name);
        EXECUTE format('ALTER TABLE %I FORCE ROW LEVEL SECURITY', table_name);
        EXECUTE format('DROP POLICY IF EXISTS %I ON %I', table_name || '_tenant_workspace_rls', table_name);
        EXECUTE format(
            'CREATE POLICY %I ON %I
               USING (
                   workspace_id::text = NULLIF(current_setting(''app.workspace_id'', true), '''')
                   AND (
                       tenant_id IS NULL
                       OR tenant_id::text = NULLIF(current_setting(''app.tenant_id'', true), '''')
                   )
               )
               WITH CHECK (
                   workspace_id::text = NULLIF(current_setting(''app.workspace_id'', true), '''')
                   AND (
                       tenant_id IS NULL
                       OR tenant_id::text = NULLIF(current_setting(''app.tenant_id'', true), '''')
                   )
               )',
            table_name || '_tenant_workspace_rls',
            table_name
        );
    END LOOP;
END $$;

INSERT INTO schema_migrations (filename, applied_at)
VALUES ('99d_intelligence_native_rls.sql', NOW())
ON CONFLICT (filename) DO NOTHING;
