-- INEGI runtime repair for existing volumes.

DO $$
DECLARE
  pw TEXT := current_setting('app.omega_cartridge_inegi_password', true);
BEGIN
  IF pw IS NULL OR pw = '' THEN
    RAISE EXCEPTION 'app.omega_cartridge_inegi_password not set; refusing INEGI runtime role repair';
  END IF;

  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'omega_cartridge_inegi') THEN
    EXECUTE format('CREATE ROLE omega_cartridge_inegi LOGIN PASSWORD %L', pw);
  ELSE
    EXECUTE format('ALTER ROLE omega_cartridge_inegi LOGIN PASSWORD %L', pw);
  END IF;
END $$;

GRANT CONNECT ON DATABASE modecissions TO omega_cartridge_inegi;
GRANT USAGE ON SCHEMA public TO omega_cartridge_inegi;
GRANT SELECT, INSERT, UPDATE ON
    cartridges, cartridge_dags, entity_config, entity_watermarks, extraction_runs
    TO omega_cartridge_inegi;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO omega_cartridge_inegi;

INSERT INTO schema_migrations (filename, applied_at)
VALUES ('99zt_inegi_runtime_role_repair.sql', NOW())
ON CONFLICT (filename) DO NOTHING;
