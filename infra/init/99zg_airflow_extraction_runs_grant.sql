-- Ensure cartridge DAG workers can persist per-entity extraction attempts.
-- Older local volumes may predate the omega_airflow_dag grants added to
-- 36_cartridge_and_meta_roles.sql, so this migration is intentionally narrow
-- and idempotent.

DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'omega_airflow_dag') THEN
        IF to_regclass('public.extraction_runs') IS NOT NULL THEN
            GRANT SELECT, INSERT, UPDATE ON TABLE public.extraction_runs TO omega_airflow_dag;
        END IF;
        GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO omega_airflow_dag;
    END IF;
END $$;

INSERT INTO schema_migrations (filename)
VALUES ('99zg_airflow_extraction_runs_grant.sql')
ON CONFLICT (filename) DO NOTHING;
