-- Sprint v1.32.1: marker for the postgres_gold least-privilege role.
--
-- Main Postgres cannot create roles inside the separate postgres_gold
-- container. The real role migration lives in infra/init_gold with the same
-- filename so fresh gold volumes run it at init time and `make migrate`
-- applies it to existing gold volumes.

DO $$
BEGIN
  RAISE NOTICE 'omega_refinement_gold role is created in postgres_gold by infra/init_gold/34_postgres_gold_role.sql';
END $$;
