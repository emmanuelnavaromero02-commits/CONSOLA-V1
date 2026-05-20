-- entity_scheduler — per-entity cron stored in entity_config.cron_expression
-- is now THE source of truth. The platform's `entity_scheduler` DAG (cada 5min)
-- queries this table, evaluates cron windows and triggers the entity's DAG via
-- the Airflow REST API with conf={entity, mode, cartridge_id}.
--
-- last_scheduled_at: idempotency anchor — never re-trigger the same fire time.

ALTER TABLE entity_config
    ADD COLUMN IF NOT EXISTS last_scheduled_at TIMESTAMPTZ;

CREATE INDEX IF NOT EXISTS entity_config_scheduled_idx
    ON entity_config (cartridge_id, entity)
    WHERE trigger_type = 'scheduled' AND cron_expression IS NOT NULL AND enabled = TRUE;
