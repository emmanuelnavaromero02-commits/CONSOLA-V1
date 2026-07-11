-- SEC EDGAR cartridge: Bronze ingestion plus governed context materialization.

DO $$
DECLARE
  pw TEXT := current_setting('app.omega_cartridge_sec_edgar_password', true);
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'omega_cartridge_sec_edgar') THEN
    IF pw IS NULL OR pw = '' THEN
      CREATE ROLE omega_cartridge_sec_edgar NOLOGIN;
      RAISE NOTICE 'omega_cartridge_sec_edgar created NOLOGIN (no password provisioned)';
    ELSE
      EXECUTE format('CREATE ROLE omega_cartridge_sec_edgar LOGIN PASSWORD %L', pw);
    END IF;
  ELSIF pw IS NOT NULL AND pw <> '' THEN
    EXECUTE format('ALTER ROLE omega_cartridge_sec_edgar LOGIN PASSWORD %L', pw);
  END IF;
END $$;

GRANT CONNECT ON DATABASE modecissions TO omega_cartridge_sec_edgar;
GRANT USAGE ON SCHEMA public TO omega_cartridge_sec_edgar;
GRANT SELECT, INSERT, UPDATE ON
    cartridges, cartridge_dags, entity_config, entity_watermarks, extraction_runs
    TO omega_cartridge_sec_edgar;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO omega_cartridge_sec_edgar;

INSERT INTO cartridges (id, name, version, description, pattern, category, bronze_path)
VALUES (
    'sec_edgar',
    'SEC EDGAR',
    '0.1.0',
    'Official SEC EDGAR company filings and XBRL facts context.',
    'dag-based',
    'cartridge',
    'raw/sec_edgar/{entity}/load_date={date}/'
)
ON CONFLICT (id) DO UPDATE
    SET name = EXCLUDED.name,
        version = EXCLUDED.version,
        description = EXCLUDED.description,
        pattern = EXCLUDED.pattern,
        category = EXCLUDED.category,
        bronze_path = EXCLUDED.bronze_path,
        updated_at = NOW();

INSERT INTO cartridge_dags (cartridge_id, dag_id, file, description, trigger, params)
VALUES (
    'sec_edgar',
    'sec_edgar_extract',
    'sec_edgar_extract.py',
    'Manual SEC EDGAR Bronze extraction.',
    'on-demand',
    '["mode","tenant_id","workspace_id","from_date","to_date","ciks"]'
)
ON CONFLICT (cartridge_id, dag_id) DO UPDATE
    SET file = EXCLUDED.file,
        description = EXCLUDED.description,
        trigger = EXCLUDED.trigger,
        params = EXCLUDED.params;

INSERT INTO entity_config
    (cartridge_id, entity, display_name, mode, primary_key, dag_id, description,
     watermark_field, watermark_format, page_size, enabled, trigger_type, cron_expression,
     connection_id)
VALUES (
    'sec_edgar',
    'company_facts',
    'SEC Company Facts',
    'incremental',
    'cik,metric_name,end_date,fiscal_year,fiscal_period,accession_number',
    'sec_edgar_extract',
    'Official SEC EDGAR XBRL facts written to immutable Bronze batches.',
    'end_date',
    'date',
    10,
    TRUE,
    'manual',
    NULL,
    'default'
)
ON CONFLICT (cartridge_id, entity) DO UPDATE
    SET display_name = EXCLUDED.display_name,
        mode = EXCLUDED.mode,
        primary_key = EXCLUDED.primary_key,
        dag_id = EXCLUDED.dag_id,
        description = EXCLUDED.description,
        watermark_field = EXCLUDED.watermark_field,
        watermark_format = EXCLUDED.watermark_format,
        page_size = EXCLUDED.page_size,
        enabled = EXCLUDED.enabled,
        trigger_type = EXCLUDED.trigger_type,
        cron_expression = EXCLUDED.cron_expression,
        connection_id = COALESCE(NULLIF(entity_config.connection_id, ''), EXCLUDED.connection_id);
