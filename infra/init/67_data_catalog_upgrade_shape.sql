-- Normalize older data_catalog installs to the MEJORAS semantic catalog shape.
-- Some beta databases created data_catalog from 19_operational_stability_hotfix
-- before 13_data_catalog.sql existed. Because both used CREATE TABLE IF NOT
-- EXISTS, those databases kept the older shape. These ALTERs are idempotent.

ALTER TABLE data_catalog
    ADD COLUMN IF NOT EXISTS id BIGSERIAL;

ALTER TABLE data_catalog
    ALTER COLUMN layer DROP NOT NULL,
    ALTER COLUMN cartridge DROP NOT NULL,
    ALTER COLUMN data_type DROP NOT NULL,
    ALTER COLUMN description DROP NOT NULL,
    ALTER COLUMN is_key SET DEFAULT FALSE,
    ALTER COLUMN is_metric SET DEFAULT FALSE;

UPDATE data_catalog SET is_key = FALSE WHERE is_key IS NULL;
UPDATE data_catalog SET is_metric = FALSE WHERE is_metric IS NULL;

CREATE UNIQUE INDEX IF NOT EXISTS data_catalog_dataset_column_uq
    ON data_catalog (dataset, column_name);
CREATE INDEX IF NOT EXISTS data_catalog_dataset_idx   ON data_catalog(dataset);
CREATE INDEX IF NOT EXISTS data_catalog_layer_idx     ON data_catalog(layer);
CREATE INDEX IF NOT EXISTS data_catalog_cartridge_idx ON data_catalog(cartridge);
CREATE INDEX IF NOT EXISTS data_catalog_tags_idx      ON data_catalog USING GIN (tags);

ALTER TABLE data_relationships
    ADD COLUMN IF NOT EXISTS id BIGSERIAL,
    ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ DEFAULT NOW();

ALTER TABLE data_relationships
    ALTER COLUMN join_hint DROP NOT NULL,
    ALTER COLUMN description DROP NOT NULL;

CREATE UNIQUE INDEX IF NOT EXISTS data_relationships_edge_uq
    ON data_relationships (from_dataset, from_column, to_dataset, to_column);
CREATE INDEX IF NOT EXISTS data_rel_from_idx ON data_relationships(from_dataset);
CREATE INDEX IF NOT EXISTS data_rel_to_idx   ON data_relationships(to_dataset);
