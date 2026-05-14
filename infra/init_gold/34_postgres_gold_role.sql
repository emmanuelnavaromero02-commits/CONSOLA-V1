-- Sprint v1.32.1: least-privilege role for Refinement on postgres_gold.
--
-- Refinement materializes master_* and gold_* tables into modecissions_gold.
-- It needs CREATE on public and DML on analytical tables, but it must not use
-- the postgres superuser.

DO $$
DECLARE
  pw TEXT := current_setting('app.omega_refinement_gold_password', true);
BEGIN
  IF pw IS NULL OR pw = '' THEN
    RAISE EXCEPTION 'app.omega_refinement_gold_password not set; refusing to create role with empty password';
  END IF;

  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'omega_refinement_gold') THEN
    EXECUTE format('CREATE ROLE omega_refinement_gold LOGIN PASSWORD %L', pw);
  END IF;
END $$;

GRANT CONNECT ON DATABASE modecissions_gold TO omega_refinement_gold;
GRANT USAGE, CREATE ON SCHEMA public TO omega_refinement_gold;
GRANT USAGE ON SCHEMA replicon TO omega_refinement_gold;
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO omega_refinement_gold;
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA replicon TO omega_refinement_gold;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO omega_refinement_gold;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA replicon TO omega_refinement_gold;

ALTER DEFAULT PRIVILEGES IN SCHEMA public
   GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO omega_refinement_gold;
ALTER DEFAULT PRIVILEGES IN SCHEMA public
   GRANT USAGE, SELECT ON SEQUENCES TO omega_refinement_gold;
