-- Banxico cartridge PR 2A: Bronze-only runtime closure.
--
-- Creates the least-privilege operational role and seeds only cartridge/DAG
-- metadata needed to trigger the manual Bronze extraction. No analytical
-- datasets, external tool registry rows, downstream state or engines.

DO $$
DECLARE
  pw TEXT := current_setting('app.omega_cartridge_banxico_password', true);
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'omega_cartridge_banxico') THEN
    IF pw IS NULL OR pw = '' THEN
      CREATE ROLE omega_cartridge_banxico NOLOGIN;
      RAISE NOTICE 'omega_cartridge_banxico created NOLOGIN (no password provisioned)';
    ELSE
      EXECUTE format('CREATE ROLE omega_cartridge_banxico LOGIN PASSWORD %L', pw);
    END IF;
  ELSIF pw IS NOT NULL AND pw <> '' THEN
    EXECUTE format('ALTER ROLE omega_cartridge_banxico LOGIN PASSWORD %L', pw);
  END IF;
END $$;

GRANT CONNECT ON DATABASE modecissions TO omega_cartridge_banxico;
GRANT USAGE ON SCHEMA public TO omega_cartridge_banxico;
GRANT SELECT, INSERT, UPDATE ON
    cartridges, cartridge_dags, entity_config, entity_watermarks, extraction_runs
    TO omega_cartridge_banxico;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO omega_cartridge_banxico;

DO $$
DECLARE
  hard_locked_tables CONSTANT text[] := ARRAY[
    'users', 'user_sessions', 'user_tokens', 'refresh_tokens',
    'tenants', 'workspaces', 'roles', 'user_workspace_roles',
    'decisions', 'decision_actions', 'audit_events', 'login_attempts',
    'vault_access_log', 'vault_entries'
  ];
  tbl text;
BEGIN
  FOREACH tbl IN ARRAY hard_locked_tables LOOP
    IF EXISTS (
      SELECT 1 FROM pg_tables
      WHERE schemaname = 'public' AND tablename = tbl
    ) THEN
      EXECUTE format('REVOKE ALL PRIVILEGES ON public.%I FROM omega_cartridge_banxico', tbl);
    END IF;
  END LOOP;
END $$;

INSERT INTO cartridges (id, name, version, description, pattern, category, bronze_path)
VALUES (
    'banxico',
    'Banco de Mexico SIE',
    '0.1.0',
    'Bronze-only ingestion for allowlisted official Banxico SIE series.',
    'dag-based',
    'cartridge',
    'raw/banxico/{entity}/load_date={date}/'
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
    'banxico',
    'banxico_extract',
    'banxico_extract.py',
    'Manual Banxico SIE Bronze-only extraction.',
    'on-demand',
    '["mode","tenant_id","workspace_id","from_date","to_date","series_ids"]'
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
    'banxico',
    'series_observations',
    'Banxico Series Observations',
    'incremental',
    'series_id,observation_date',
    'banxico_extract',
    'Official Banxico SIE observations written to immutable Bronze batches.',
    'observation_date',
    'date',
    20,
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
