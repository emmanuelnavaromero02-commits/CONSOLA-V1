-- Sprint v1.31: SAP cartridge entity metadata columns.
--
-- SAP catalog_service imports YAML fields such as select_fields,
-- page_size, effective_dated and protection into entity_config.
-- Keep this idempotent so existing environments can apply it manually
-- and fresh Postgres volumes get the same schema from init order.

ALTER TABLE entity_config ADD COLUMN IF NOT EXISTS watermark_format TEXT;
ALTER TABLE entity_config ADD COLUMN IF NOT EXISTS page_size INTEGER;
ALTER TABLE entity_config ADD COLUMN IF NOT EXISTS select_fields JSONB;
ALTER TABLE entity_config ADD COLUMN IF NOT EXISTS protection JSONB;
ALTER TABLE entity_config ADD COLUMN IF NOT EXISTS effective_dated BOOLEAN DEFAULT FALSE;
ALTER TABLE entity_config ADD COLUMN IF NOT EXISTS date_field TEXT;
ALTER TABLE entity_config ADD COLUMN IF NOT EXISTS future_window_days INTEGER;
