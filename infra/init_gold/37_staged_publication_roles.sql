-- Staged publication authority. Reader and publisher credentials are distinct.
DO $$
DECLARE
  publisher_pw text := current_setting('app.omega_gold_publisher_password', true);
  reader_pw text := current_setting('app.omega_refinement_gold_password', true);
BEGIN
  IF publisher_pw IS NULL OR publisher_pw = '' THEN
    RAISE EXCEPTION 'app.omega_gold_publisher_password not set';
  END IF;
  IF reader_pw IS NOT NULL AND publisher_pw = reader_pw THEN
    RAISE EXCEPTION 'Gold publisher and reader credentials must be distinct';
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'omega_gold_owner') THEN
    CREATE ROLE omega_gold_owner NOLOGIN NOINHERIT NOBYPASSRLS;
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'omega_gold_publisher') THEN
    EXECUTE format(
      'CREATE ROLE omega_gold_publisher LOGIN NOINHERIT NOBYPASSRLS PASSWORD %L',
      publisher_pw
    );
  END IF;
END $$;

ALTER ROLE omega_gold_owner NOLOGIN NOINHERIT NOBYPASSRLS NOSUPERUSER
  NOCREATEDB NOCREATEROLE NOREPLICATION;
ALTER ROLE omega_refinement_gold LOGIN NOINHERIT NOBYPASSRLS NOSUPERUSER
  NOCREATEDB NOCREATEROLE NOREPLICATION;
ALTER ROLE omega_gold_publisher LOGIN NOINHERIT NOBYPASSRLS NOSUPERUSER
  NOCREATEDB NOCREATEROLE NOREPLICATION;

GRANT CONNECT ON DATABASE modecissions_gold TO omega_gold_publisher;
GRANT USAGE ON SCHEMA public TO omega_refinement_gold, omega_gold_publisher;
GRANT USAGE, CREATE ON SCHEMA public TO omega_gold_owner;
REVOKE CREATE ON SCHEMA public FROM PUBLIC, omega_refinement_gold, omega_gold_publisher;
REVOKE INSERT, UPDATE, DELETE, TRUNCATE ON ALL TABLES IN SCHEMA public
  FROM omega_refinement_gold;
REVOKE SELECT ON ALL TABLES IN SCHEMA public FROM omega_refinement_gold;
REVOKE USAGE, UPDATE ON ALL SEQUENCES IN SCHEMA public FROM omega_refinement_gold;
REVOKE EXECUTE ON FUNCTION public.omega_apply_gold_rls_for_table(text)
  FROM PUBLIC, omega_refinement_gold, omega_gold_publisher;
GRANT EXECUTE ON FUNCTION public.omega_apply_gold_rls_for_table(text)
  TO omega_gold_owner;

ALTER DEFAULT PRIVILEGES FOR ROLE postgres IN SCHEMA public
  REVOKE INSERT, UPDATE, DELETE, TRUNCATE ON TABLES FROM omega_refinement_gold;
ALTER DEFAULT PRIVILEGES FOR ROLE postgres IN SCHEMA public
  REVOKE SELECT ON TABLES FROM omega_refinement_gold;

DO $$
DECLARE
  relation record;
  policy record;
BEGIN
  FOR relation IN
    SELECT c.oid::regclass AS relation_name, c.relname, c.relkind
      FROM pg_class c
      JOIN pg_namespace n ON n.oid = c.relnamespace
     WHERE n.nspname = 'public'
       AND c.relname LIKE 'gold\_%' ESCAPE '\'
       AND c.relkind IN ('r', 'p')
  LOOP
    EXECUTE format('ALTER TABLE %s OWNER TO omega_gold_owner', relation.relation_name);
    FOR policy IN
      SELECT policyname FROM pg_policies
       WHERE schemaname='public' AND tablename=relation.relname
    LOOP
      EXECUTE format(
        'DROP POLICY %I ON %s', policy.policyname, relation.relation_name
      );
    END LOOP;
    PERFORM public.omega_apply_gold_rls_for_table(relation.relname);
    EXECUTE format('GRANT SELECT ON %s TO omega_refinement_gold', relation.relation_name);
  END LOOP;
END $$;
