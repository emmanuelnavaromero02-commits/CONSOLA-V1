-- MODecissionsPaaS — operational stability hotfix
-- Idempotent fixes for existing local/staging volumes.

-- Security tables may be absent in already-created Docker volumes.
CREATE TABLE IF NOT EXISTS audit_events (
    id            BIGSERIAL PRIMARY KEY,
    user_id       BIGINT,
    email         TEXT,
    action        TEXT NOT NULL,
    resource_type TEXT,
    resource_id   TEXT,
    ip            TEXT,
    user_agent    TEXT,
    status        TEXT,
    metadata      JSONB,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS login_attempts (
    id          BIGSERIAL PRIMARY KEY,
    email       TEXT NOT NULL,
    ip          TEXT,
    success     BOOLEAN NOT NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_login_attempts_email_created_at
    ON login_attempts (email, created_at);

-- Semantic catalog used by refinement / Studio. Must exist even when empty.
CREATE TABLE IF NOT EXISTS data_catalog (
    dataset        TEXT NOT NULL,
    layer          TEXT NOT NULL DEFAULT 'silver',
    cartridge      TEXT NOT NULL DEFAULT '',
    column_name    TEXT NOT NULL,
    data_type      TEXT NOT NULL DEFAULT '',
    description    TEXT NOT NULL DEFAULT '',
    example_values JSONB,
    tags           TEXT[] NOT NULL DEFAULT '{}',
    is_key         BOOLEAN,
    is_metric      BOOLEAN,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (dataset, column_name)
);

CREATE INDEX IF NOT EXISTS idx_data_catalog_layer
    ON data_catalog (layer);
CREATE INDEX IF NOT EXISTS idx_data_catalog_cartridge
    ON data_catalog (cartridge);
CREATE INDEX IF NOT EXISTS idx_data_catalog_tags
    ON data_catalog USING GIN (tags);

CREATE TABLE IF NOT EXISTS data_relationships (
    from_dataset TEXT NOT NULL,
    from_column  TEXT NOT NULL,
    to_dataset   TEXT NOT NULL,
    to_column    TEXT NOT NULL,
    join_hint    TEXT NOT NULL DEFAULT 'LEFT',
    description  TEXT NOT NULL DEFAULT '',
    transform    TEXT,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (from_dataset, from_column, to_dataset, to_column)
);

-- Replicon Analytics entities must use the generic extraction DAG.
-- ProjectDetail remains on the Services-specific DAG.
UPDATE entity_config
SET connection_id = 'analytics',
    dag_id = 'replicon_extract'
WHERE cartridge_id = 'replicon'
  AND entity <> 'ProjectDetail';

UPDATE entity_config
SET connection_id = 'services',
    dag_id = 'replicon_projects_detail'
WHERE cartridge_id = 'replicon'
  AND entity = 'ProjectDetail';
