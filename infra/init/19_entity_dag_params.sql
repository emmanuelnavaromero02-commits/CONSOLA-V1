-- entity_config.dag_params: per-entity params passed to the DAG via dag_run.conf.
-- Used by generic DAGs (e.g. `file_ingest`) where the same DAG file serves
-- many entities, each with its own filename pattern / parser / sheet / etc.
--
-- Example for replicon ProjectsBilling:
--   {"file_pattern": "projects_billing*.csv", "format": "csv",
--    "delimiter": ",", "encoding": "utf-8"}
--
-- Example for SAP HCM extract that needs latin1:
--   {"file_pattern": "sap_hcm*.csv", "format": "csv",
--    "encoding": "latin1", "delimiter": ";"}
--
-- The entity_scheduler merges dag_params into the dagRun conf:
--   conf = {entity, mode, cartridge_id, **dag_params}

ALTER TABLE entity_config
    ADD COLUMN IF NOT EXISTS dag_params JSONB NOT NULL DEFAULT '{}';
