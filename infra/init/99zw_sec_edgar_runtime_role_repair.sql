-- SEC EDGAR runtime repair for existing volumes.

DO $$
DECLARE
  pw TEXT := current_setting('app.omega_cartridge_sec_edgar_password', true);
BEGIN
  IF pw IS NULL OR pw = '' THEN
    RAISE EXCEPTION 'app.omega_cartridge_sec_edgar_password not set; refusing SEC EDGAR runtime role repair';
  END IF;

  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'omega_cartridge_sec_edgar') THEN
    EXECUTE format('CREATE ROLE omega_cartridge_sec_edgar LOGIN PASSWORD %L', pw);
  ELSE
    EXECUTE format('ALTER ROLE omega_cartridge_sec_edgar LOGIN PASSWORD %L', pw);
  END IF;
END $$;

GRANT CONNECT ON DATABASE modecissions TO omega_cartridge_sec_edgar;
GRANT USAGE ON SCHEMA public TO omega_cartridge_sec_edgar;
GRANT SELECT, INSERT, UPDATE ON
    cartridges, cartridge_dags, entity_config, entity_watermarks, extraction_runs
    TO omega_cartridge_sec_edgar;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO omega_cartridge_sec_edgar;

INSERT INTO schema_migrations (filename, applied_at)
VALUES ('99zw_sec_edgar_runtime_role_repair.sql', NOW())
ON CONFLICT (filename) DO NOTHING;
