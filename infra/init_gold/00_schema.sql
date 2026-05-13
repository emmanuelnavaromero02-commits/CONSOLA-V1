-- OMEGA Gold DB — published silver/gold datasets
--
-- This database (Postgres 'modecissions_gold') is initialised by Postgres
-- with all .sql files in this directory. Cartridges then create their own
-- schemas (replicon., sap_hcm., etc.) on first boot via app-level migrations.
--
-- Tables created here are READ-MOSTLY views consumed by the Workspace,
-- Refinement (DuckDB ATTACH), and Studio (semantic layer). Writes happen
-- via cartridge ingestion DAGs (Airflow), never directly.
--
-- DO NOT add tables here directly — they belong inside cartridge schemas.

-- Replicon silver/gold schema (created by replicon cartridge on boot)
CREATE SCHEMA IF NOT EXISTS replicon;

-- Placeholder: tables are created by cartridges via schema migration on startup.
-- Example tables the replicon cartridge will create:
--   replicon.timeentry_silver
--   replicon.timesheet_summary_gold
--   replicon.project_hours_gold
