-- Phase 7 (Descubrimiento y perfilado automático): per-column profiling stats.
-- Adds durable columns to the semantic catalog so each materialization can
-- persist real, data-derived statistics (null_rate, distinct_count, min/max)
-- computed by a single DuckDB SUMMARIZE pass over the landed silver/gold data.
-- All idempotent (ADD COLUMN IF NOT EXISTS) so it is safe on existing beta DBs;
-- data_catalog is created earlier in 13_data_catalog.sql.

ALTER TABLE data_catalog ADD COLUMN IF NOT EXISTS null_rate      DOUBLE PRECISION;
ALTER TABLE data_catalog ADD COLUMN IF NOT EXISTS distinct_count BIGINT;
ALTER TABLE data_catalog ADD COLUMN IF NOT EXISTS min_value      TEXT;
ALTER TABLE data_catalog ADD COLUMN IF NOT EXISTS max_value      TEXT;
ALTER TABLE data_catalog ADD COLUMN IF NOT EXISTS profiled_at    TIMESTAMPTZ;
