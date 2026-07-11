-- Banxico runtime repair for existing volumes.
--
-- 95_banxico_role_and_seed.sql allowed a NOLOGIN role while PR 2A was blocked
-- by environment. Once the AWS same-host cartridge is enabled, production needs
-- a real login role. This migration is narrow and idempotent.

DO $$
DECLARE
  pw TEXT := current_setting('app.omega_cartridge_banxico_password', true);
BEGIN
  IF pw IS NULL OR pw = '' THEN
    RAISE EXCEPTION 'app.omega_cartridge_banxico_password not set; refusing Banxico runtime role repair';
  END IF;

  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'omega_cartridge_banxico') THEN
    EXECUTE format('CREATE ROLE omega_cartridge_banxico LOGIN PASSWORD %L', pw);
  ELSE
    EXECUTE format('ALTER ROLE omega_cartridge_banxico LOGIN PASSWORD %L', pw);
  END IF;
END $$;

GRANT CONNECT ON DATABASE modecissions TO omega_cartridge_banxico;
GRANT USAGE ON SCHEMA public TO omega_cartridge_banxico;
GRANT SELECT, INSERT, UPDATE ON
    cartridges, cartridge_dags, entity_config, entity_watermarks, extraction_runs
    TO omega_cartridge_banxico;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO omega_cartridge_banxico;

INSERT INTO schema_migrations (filename, applied_at)
VALUES ('99zq_banxico_runtime_role_repair.sql', NOW())
ON CONFLICT (filename) DO NOTHING;
