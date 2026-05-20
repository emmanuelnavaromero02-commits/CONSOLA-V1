-- cartridge_dags.dag_params_example: optional example payload shown by Studio
-- for generic/shared DAGs such as file_ingest, entity_scheduler,
-- dataset_refresh_chain and agent_runner. This keeps the MEJORAS 2
-- cartridge_service contract safe on
-- existing databases whose base schema predates the column.

ALTER TABLE cartridge_dags
    ADD COLUMN IF NOT EXISTS dag_params_example JSONB NOT NULL DEFAULT '{}';
