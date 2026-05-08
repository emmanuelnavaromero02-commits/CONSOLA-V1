-- MODecissionsPaaS — base schema

-- MCP server registry
CREATE TABLE IF NOT EXISTS mcp_servers (
    id          TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    url         TEXT NOT NULL,
    category    TEXT NOT NULL,  -- workflow | cartridge | analytics | refinement
    description TEXT,
    tools       JSONB DEFAULT '[]',
    healthy     BOOLEAN DEFAULT false,
    registered_at TIMESTAMPTZ DEFAULT NOW(),
    last_seen   TIMESTAMPTZ DEFAULT NOW()
);

-- Dataset definitions (silver / master / gold)
CREATE TABLE IF NOT EXISTS datasets (
    name           TEXT PRIMARY KEY,
    description    TEXT NOT NULL DEFAULT '',
    layer          TEXT NOT NULL,
    cartridge      TEXT NOT NULL DEFAULT '',
    sources        JSONB NOT NULL DEFAULT '[]',
    sql_def        TEXT NOT NULL DEFAULT '',
    column_mapping JSONB NOT NULL DEFAULT '{}',
    schedule       TEXT,
    last_refresh   TIMESTAMPTZ,
    row_count      BIGINT,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at     TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Ingest run logs (from cartridges via MCP)
CREATE TABLE IF NOT EXISTS run_logs (
    id          BIGSERIAL PRIMARY KEY,
    run_id      TEXT NOT NULL,
    cartridge   TEXT NOT NULL,
    entity      TEXT,
    level       TEXT DEFAULT 'INFO',
    status      TEXT,
    message     TEXT,
    detail      JSONB,
    ts          TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_run_logs_run_id ON run_logs(run_id);
CREATE INDEX IF NOT EXISTS idx_run_logs_cartridge ON run_logs(cartridge, ts DESC);

-- Cartridge: entity configuration
CREATE TABLE IF NOT EXISTS entity_config (
    cartridge_id        TEXT NOT NULL,
    entity              TEXT NOT NULL,
    display_name        TEXT,                  -- human-readable label for UI / reports
    mode                TEXT DEFAULT 'full',   -- full | incremental
    description         TEXT,
    enabled             BOOLEAN DEFAULT TRUE,
    primary_key         TEXT,
    dag_id              TEXT,                  -- which DAG handles this entity
    trigger_type        TEXT DEFAULT 'manual', -- manual | scheduled
    cron_expression     TEXT,                  -- cron string when trigger_type='scheduled'
    -- legacy columns kept for backwards compat, no longer used by Studio:
    watermark_field     TEXT,
    connection_id       TEXT,
    PRIMARY KEY (cartridge_id, entity)
);

-- Migrate existing installations (no-op if columns already exist)
ALTER TABLE entity_config ADD COLUMN IF NOT EXISTS display_name     TEXT;
ALTER TABLE entity_config ADD COLUMN IF NOT EXISTS primary_key      TEXT;
ALTER TABLE entity_config ADD COLUMN IF NOT EXISTS dag_id           TEXT;
ALTER TABLE entity_config ADD COLUMN IF NOT EXISTS trigger_type     TEXT DEFAULT 'manual';
ALTER TABLE entity_config ADD COLUMN IF NOT EXISTS cron_expression  TEXT;
ALTER TABLE entity_config ADD COLUMN IF NOT EXISTS connection_id    TEXT;
ALTER TABLE entity_config ADD COLUMN IF NOT EXISTS watermark_field  TEXT;

-- Replicon cartridge: watermarks per entity
CREATE TABLE IF NOT EXISTS entity_watermarks (
    cartridge_id         TEXT NOT NULL,
    entity_name          TEXT NOT NULL,
    watermark_field      TEXT,
    last_watermark_value TEXT,
    last_run_id          TEXT,
    updated_at           TIMESTAMPTZ DEFAULT NOW(),
    PRIMARY KEY (cartridge_id, entity_name)
);

-- Replicon cartridge: knowledge bit configuration
CREATE TABLE IF NOT EXISTS kb_config (
    cartridge_id TEXT NOT NULL,
    kb_id        TEXT NOT NULL,
    name         TEXT,
    description  TEXT,
    sql          TEXT,
    pg_table     TEXT,
    output_path  TEXT,
    enabled      BOOLEAN DEFAULT TRUE,
    PRIMARY KEY (cartridge_id, kb_id)
);

-- Replicon cartridge: extraction run history
CREATE TABLE IF NOT EXISTS extraction_runs (
    run_id             TEXT PRIMARY KEY,
    cartridge_id       TEXT,
    entity_name        TEXT,
    run_type           TEXT,
    status             TEXT,
    records_extracted  INTEGER,
    storage_uri        TEXT,
    error_message      TEXT,
    started_at         TIMESTAMPTZ,
    finished_at        TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_extraction_runs_cartridge ON extraction_runs(cartridge_id, started_at DESC);

-- Cartridge registry
CREATE TABLE IF NOT EXISTS cartridges (
    id          TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    version     TEXT NOT NULL DEFAULT '1.0.0',
    description TEXT,
    pattern     TEXT NOT NULL DEFAULT 'dag-based',  -- dag-based | fastapi-mcp
    category    TEXT NOT NULL DEFAULT 'cartridge',
    bronze_path TEXT,
    created_at  TIMESTAMPTZ DEFAULT NOW(),
    updated_at  TIMESTAMPTZ DEFAULT NOW()
);

-- Cartridge API connection definitions (no credentials — those live in Vault)
CREATE TABLE IF NOT EXISTS cartridge_connections (
    cartridge_id  TEXT NOT NULL,
    conn_id       TEXT NOT NULL,
    description   TEXT,
    auth_type     TEXT NOT NULL DEFAULT 'bearer_token',
    poll_strategy TEXT,
    enabled       BOOLEAN DEFAULT TRUE,
    PRIMARY KEY (cartridge_id, conn_id)
);

-- Cartridge DAG definitions
CREATE TABLE IF NOT EXISTS cartridge_dags (
    cartridge_id TEXT NOT NULL,
    dag_id       TEXT NOT NULL,
    file         TEXT,
    description  TEXT,
    trigger      TEXT DEFAULT 'on-demand',
    params       JSONB DEFAULT '[]',
    source_code  TEXT,                         -- Python source kept for AI-assisted modification
    updated_at   TIMESTAMPTZ DEFAULT NOW(),
    PRIMARY KEY (cartridge_id, dag_id)
);

ALTER TABLE cartridge_dags ADD COLUMN IF NOT EXISTS source_code TEXT;
ALTER TABLE cartridge_dags ADD COLUMN IF NOT EXISTS updated_at  TIMESTAMPTZ DEFAULT NOW();

-- Semantic vocabulary per cartridge
CREATE TABLE IF NOT EXISTS semantic_terms (
    id           BIGSERIAL PRIMARY KEY,
    cartridge_id TEXT NOT NULL,
    term         TEXT NOT NULL,
    definition   TEXT,
    maps_to      TEXT,
    UNIQUE (cartridge_id, term)
);

-- Background job queue
CREATE TABLE IF NOT EXISTS jobs (
    job_id      TEXT PRIMARY KEY,
    tool        TEXT NOT NULL,
    args        JSONB DEFAULT '{}',
    status      TEXT DEFAULT 'running',
    message     TEXT,
    result      JSONB,
    error       TEXT,
    created_at  TIMESTAMPTZ DEFAULT NOW(),
    updated_at  TIMESTAMPTZ DEFAULT NOW(),
    finished_at TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(status, created_at DESC);

-- LLM token usage tracking
CREATE TABLE IF NOT EXISTS token_usage (
    id            BIGSERIAL PRIMARY KEY,
    provider      TEXT NOT NULL,
    model         TEXT NOT NULL,
    input_tokens  INTEGER NOT NULL DEFAULT 0,
    output_tokens INTEGER NOT NULL DEFAULT 0,
    cache_creation_tokens INTEGER NOT NULL DEFAULT 0,
    cache_read_tokens INTEGER NOT NULL DEFAULT 0,
    ts            TIMESTAMPTZ DEFAULT NOW()
);
ALTER TABLE token_usage ADD COLUMN IF NOT EXISTS cache_creation_tokens INTEGER NOT NULL DEFAULT 0;
ALTER TABLE token_usage ADD COLUMN IF NOT EXISTS cache_read_tokens INTEGER NOT NULL DEFAULT 0;

-- Silver lineage — trazabilidad de cada materialización silver
CREATE TABLE IF NOT EXISTS silver_lineage (
    id              BIGSERIAL PRIMARY KEY,
    silver_name     TEXT NOT NULL,          -- e.g. "empleados_activos"
    cartridge_id    TEXT NOT NULL,          -- e.g. "replicon", "ssff"
    source_entity   TEXT NOT NULL,          -- e.g. "raw/replicon/User"
    source_load_date DATE,                  -- partición bronze origen
    source_batch_id TEXT,                   -- batch_id bronze origen
    sql_def         TEXT NOT NULL,          -- SQL exacto que produjo el silver
    column_mapping  JSONB DEFAULT '{}',     -- {"src_col": "business_term", ...}
    layer           TEXT DEFAULT 'silver',  -- silver | master
    row_count       BIGINT,
    storage_uri     TEXT,                   -- s3://lakehouse/silver/... o tabla PG
    created_by      TEXT DEFAULT 'llm',
    created_at      TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_silver_lineage_cartridge
    ON silver_lineage(cartridge_id, silver_name, created_at DESC);

-- Vault persistent store
-- scope = 'connections' | 'secrets'
-- cartridge = 'replicon' | 'console' | ...
-- key = connection_id or secret_key
-- value = JSONB with full config (credentials included)
CREATE TABLE IF NOT EXISTS vault_entries (
    scope      TEXT NOT NULL,
    cartridge  TEXT NOT NULL,
    key        TEXT NOT NULL,
    value      JSONB NOT NULL DEFAULT '{}',
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    PRIMARY KEY (scope, cartridge, key)
);

-- Pipeline run statistics (written by every DAG at completion)
CREATE TABLE IF NOT EXISTS pipeline_runs (
    run_id              TEXT PRIMARY KEY,
    dag_id              TEXT NOT NULL,
    cartridge_id        TEXT NOT NULL,
    entity              TEXT NOT NULL,
    airflow_dag_run_id  TEXT,                  -- Airflow run_id (e.g. manual__2024-01-15T…) for direct log lookup
    mode                TEXT,                  -- full | incremental
    status              TEXT NOT NULL,         -- success | failed | partial
    started_at          TIMESTAMPTZ,
    finished_at         TIMESTAMPTZ,
    duration_seconds    NUMERIC,
    record_count        INTEGER,
    bytes_written       BIGINT,
    storage_uri         TEXT,
    watermark_updated_to TEXT,
    error_message       TEXT,
    extra               JSONB DEFAULT '{}'     -- any additional stats the DAG wants to store
);

CREATE INDEX IF NOT EXISTS idx_pipeline_runs_cartridge
    ON pipeline_runs(cartridge_id, entity, started_at DESC);
CREATE INDEX IF NOT EXISTS idx_pipeline_runs_dag
    ON pipeline_runs(dag_id, started_at DESC);

-- Custom MCP tools (defined via UI or config)
CREATE TABLE IF NOT EXISTS mcp_custom_tools (
    id           BIGSERIAL PRIMARY KEY,
    cartridge_id TEXT NOT NULL,
    name         TEXT NOT NULL,
    description  TEXT,
    tool_type    TEXT NOT NULL,
    config       JSONB DEFAULT '{}',
    enabled      BOOLEAN DEFAULT TRUE,
    created_at   TIMESTAMPTZ DEFAULT NOW()
);
