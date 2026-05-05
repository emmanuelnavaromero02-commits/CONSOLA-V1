-- MODecissions Gold DB — published silver/gold datasets
-- Each cartridge creates its own schema here on startup.

-- Replicon silver/gold schema (created by replicon cartridge on boot)
CREATE SCHEMA IF NOT EXISTS replicon;

-- Placeholder: tables are created by cartridges via schema migration on startup.
-- Example tables the replicon cartridge will create:
--   replicon.timeentry_silver
--   replicon.timesheet_summary_gold
--   replicon.project_hours_gold
