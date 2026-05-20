-- Semantic data catalog: business descriptions, tags and relationships
-- across datasets/columns. Read by `get_data_catalog`, written by
-- `upsert_catalog_entries` and `register_relationship` in refinement.

CREATE TABLE IF NOT EXISTS data_catalog (
    id              SERIAL PRIMARY KEY,
    dataset         TEXT NOT NULL,
    layer           TEXT,
    cartridge       TEXT,
    column_name     TEXT NOT NULL,
    data_type       TEXT,
    description     TEXT,
    example_values  JSONB,
    tags            TEXT[] DEFAULT '{}',
    is_key          BOOLEAN DEFAULT FALSE,
    is_metric       BOOLEAN DEFAULT FALSE,
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    updated_at      TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE (dataset, column_name)
);

CREATE INDEX IF NOT EXISTS data_catalog_dataset_idx   ON data_catalog(dataset);
CREATE INDEX IF NOT EXISTS data_catalog_layer_idx     ON data_catalog(layer);
CREATE INDEX IF NOT EXISTS data_catalog_cartridge_idx ON data_catalog(cartridge);
CREATE INDEX IF NOT EXISTS data_catalog_tags_idx      ON data_catalog USING GIN (tags);


CREATE TABLE IF NOT EXISTS data_relationships (
    id              SERIAL PRIMARY KEY,
    from_dataset    TEXT NOT NULL,
    from_column     TEXT NOT NULL,
    to_dataset      TEXT NOT NULL,
    to_column       TEXT NOT NULL,
    join_hint       TEXT,
    description     TEXT,
    transform       TEXT,
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE (from_dataset, from_column, to_dataset, to_column)
);

CREATE INDEX IF NOT EXISTS data_rel_from_idx ON data_relationships(from_dataset);
CREATE INDEX IF NOT EXISTS data_rel_to_idx   ON data_relationships(to_dataset);
