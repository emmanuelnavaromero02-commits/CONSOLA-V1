-- Sprint v1.38 — dedicated DB roles for SAP / Airflow / Superset
-- (audit B5 + B6, P0.5).
--
-- Pre-v1.38 the three SAP cartridges plus the Airflow webserver /
-- scheduler / init plus Superset all connected as the ``postgres``
-- superuser. Any one of them being compromised (dep CVE, RCE in a
-- parser, container escape) would hand the attacker:
--
--   * read/write on every public table including users, decisions,
--     vault_entries (super bypasses the per-role REVOKEs from v1.19
--     and v1.36),
--   * CREATE ROLE / ALTER ROLE,
--   * bypass of every row-level filter we add later.
--
-- v1.38 introduces six least-privilege roles:
--
--   * omega_cartridge_sap_hcm   — SAP HCM cartridge runtime,
--   * omega_cartridge_sap_s4    — SAP S/4HANA cartridge runtime,
--   * omega_cartridge_sap_sf    — SAP SuccessFactors cartridge runtime,
--   * omega_airflow_dag         — what Airflow DAGs use when they
--                                 talk to the operational DB
--                                 (``modecissions``) via the
--                                 ``AIRFLOW_VAR_POSTGRES_CONN``
--                                 variable; today no DAG actually
--                                 reaches the DB directly (everything
--                                 goes through mcp-infra), but the
--                                 variable IS provisioned, so this
--                                 caps the blast radius if a future
--                                 DAG starts using it.
--   * omega_airflow_meta        — Airflow's own metastore (the DB
--                                 ``airflow``). Created here as a
--                                 global role; the per-DB grants run
--                                 in the airflow-init container after
--                                 ``airflow db migrate`` so the role
--                                 owns the tables that Airflow just
--                                 created.
--   * omega_superset_meta       — Superset's own metastore (the DB
--                                 ``superset``). Same model as
--                                 airflow_meta — bootstrap stays as
--                                 ``postgres`` (one-shot), runtime
--                                 uses the dedicated role.
--
-- All six roles refuse to be created with an empty password (same
-- defense-in-depth as v1.19): the password comes from the
-- ``app.omega_<name>_password`` GUC that infra/docker-compose.yml
-- forwards via PGOPTIONS.
--
-- The hard line from v1.19 / v1.36 stays in place: NONE of these
-- roles can read users / tenants / decisions / vault_entries. The
-- explicit REVOKE at the bottom catches any future
-- ``GRANT ON ALL TABLES`` that forgets to exclude them.

-- ─────────────────────────────────────────────────────────────
-- Helper: create role only if missing, with password from GUC.
-- ─────────────────────────────────────────────────────────────

DO $$
DECLARE
  pw TEXT := current_setting('app.omega_cartridge_sap_hcm_password', true);
BEGIN
  IF pw IS NULL OR pw = '' THEN
    RAISE EXCEPTION 'app.omega_cartridge_sap_hcm_password not set; refusing to create role with empty password';
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'omega_cartridge_sap_hcm') THEN
    EXECUTE format('CREATE ROLE omega_cartridge_sap_hcm LOGIN PASSWORD %L', pw);
  END IF;
END $$;

DO $$
DECLARE
  pw TEXT := current_setting('app.omega_cartridge_sap_s4_password', true);
BEGIN
  IF pw IS NULL OR pw = '' THEN
    RAISE EXCEPTION 'app.omega_cartridge_sap_s4_password not set; refusing to create role with empty password';
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'omega_cartridge_sap_s4') THEN
    EXECUTE format('CREATE ROLE omega_cartridge_sap_s4 LOGIN PASSWORD %L', pw);
  END IF;
END $$;

DO $$
DECLARE
  pw TEXT := current_setting('app.omega_cartridge_sap_sf_password', true);
BEGIN
  IF pw IS NULL OR pw = '' THEN
    RAISE EXCEPTION 'app.omega_cartridge_sap_sf_password not set; refusing to create role with empty password';
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'omega_cartridge_sap_sf') THEN
    EXECUTE format('CREATE ROLE omega_cartridge_sap_sf LOGIN PASSWORD %L', pw);
  END IF;
END $$;

DO $$
DECLARE
  pw TEXT := current_setting('app.omega_airflow_dag_password', true);
BEGIN
  IF pw IS NULL OR pw = '' THEN
    RAISE EXCEPTION 'app.omega_airflow_dag_password not set; refusing to create role with empty password';
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'omega_airflow_dag') THEN
    EXECUTE format('CREATE ROLE omega_airflow_dag LOGIN PASSWORD %L', pw);
  END IF;
END $$;

DO $$
DECLARE
  pw TEXT := current_setting('app.omega_airflow_meta_password', true);
BEGIN
  IF pw IS NULL OR pw = '' THEN
    RAISE EXCEPTION 'app.omega_airflow_meta_password not set; refusing to create role with empty password';
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'omega_airflow_meta') THEN
    EXECUTE format('CREATE ROLE omega_airflow_meta LOGIN PASSWORD %L', pw);
  END IF;
END $$;

DO $$
DECLARE
  pw TEXT := current_setting('app.omega_superset_meta_password', true);
BEGIN
  IF pw IS NULL OR pw = '' THEN
    RAISE EXCEPTION 'app.omega_superset_meta_password not set; refusing to create role with empty password';
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'omega_superset_meta') THEN
    EXECUTE format('CREATE ROLE omega_superset_meta LOGIN PASSWORD %L', pw);
  END IF;
END $$;

-- ─────────────────────────────────────────────────────────────
-- kb_runs — referenced by every SAP cartridge's kb_service but
-- not defined in any prior init script. Audit B5+B6 reviewer #2
-- flagged this as a runtime-blocking bug pre-existing v1.38. We
-- create it here (idempotent) so the cartridge GRANTs below have
-- something to bind to.
-- ─────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS kb_runs (
    run_id          TEXT PRIMARY KEY,
    kb_id           TEXT NOT NULL,
    status          TEXT NOT NULL,
    records_output  INTEGER,
    storage_uri     TEXT,
    error_message   TEXT,
    started_at      TIMESTAMPTZ,
    finished_at     TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS idx_kb_runs_kb_id_started
    ON kb_runs(kb_id, started_at DESC);

-- ─────────────────────────────────────────────────────────────
-- omega_cartridge_sap_* — read/write the operational catalog and
-- their own run/watermark/log rows; never touch identity / decisions.
--
-- Grep of ``cartridges/sap_*/app/`` confirms the SQL surface is
-- limited to: cartridges (catalog row), entity_config / kb_config
-- (config rows), entity_watermarks (per-entity watermark UPSERT),
-- extraction_runs / kb_runs / jobs (run logs), run_logs (per-entity
-- log lines), and SELECT on mcp_servers / mcp_custom_tools for the
-- custom-tools loader.
-- ─────────────────────────────────────────────────────────────
GRANT CONNECT ON DATABASE modecissions TO
    omega_cartridge_sap_hcm,
    omega_cartridge_sap_s4,
    omega_cartridge_sap_sf;
GRANT USAGE ON SCHEMA public TO
    omega_cartridge_sap_hcm,
    omega_cartridge_sap_s4,
    omega_cartridge_sap_sf;

GRANT SELECT, INSERT, UPDATE ON
    cartridges, entity_config, kb_config,
    entity_watermarks, extraction_runs, kb_runs, jobs, run_logs
    TO omega_cartridge_sap_hcm,
       omega_cartridge_sap_s4,
       omega_cartridge_sap_sf;

GRANT SELECT ON
    mcp_servers, mcp_custom_tools
    TO omega_cartridge_sap_hcm,
       omega_cartridge_sap_s4,
       omega_cartridge_sap_sf;

GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO
    omega_cartridge_sap_hcm,
    omega_cartridge_sap_s4,
    omega_cartridge_sap_sf;

-- Defense-in-depth: a future ``GRANT ON ALL TABLES`` that forgets
-- to exclude these would silently re-open the path; an explicit
-- REVOKE here keeps the cartridge roles permanently locked out of
-- identity / auth / decisions / vault.
DO $$
DECLARE
  hard_locked_tables CONSTANT text[] := ARRAY[
    'users', 'user_sessions', 'user_tokens', 'refresh_tokens',
    'tenants', 'workspaces', 'roles', 'user_workspace_roles',
    'decisions', 'decision_actions',
    'audit_events', 'login_attempts', 'vault_access_log',
    'vault_entries'
  ];
  cartridge_roles CONSTANT text[] := ARRAY[
    'omega_cartridge_sap_hcm',
    'omega_cartridge_sap_s4',
    'omega_cartridge_sap_sf',
    'omega_airflow_dag'
  ];
  tbl text;
  role_name text;
BEGIN
  FOREACH role_name IN ARRAY cartridge_roles LOOP
    -- Reviewer #1 (DB security): skip the role entirely if it
    -- somehow doesn't exist yet. The four CREATE ROLE blocks above
    -- normally create them, but if the operator runs this file
    -- standalone against a DB where the CREATE step was rolled
    -- back, ``REVOKE ... FROM <missing role>`` would abort the
    -- whole transaction.
    IF NOT EXISTS (
      SELECT 1 FROM pg_roles WHERE rolname = role_name
    ) THEN
      RAISE NOTICE '% does not exist; skipping REVOKE block', role_name;
      CONTINUE;
    END IF;
    FOREACH tbl IN ARRAY hard_locked_tables LOOP
      IF EXISTS (
        SELECT 1 FROM pg_tables
        WHERE schemaname = 'public' AND tablename = tbl
      ) THEN
        EXECUTE format(
          'REVOKE ALL PRIVILEGES ON public.%I FROM %I',
          tbl, role_name
        );
      END IF;
    END LOOP;
  END LOOP;
END $$;

-- ─────────────────────────────────────────────────────────────
-- omega_airflow_dag — what DAGs use when they reach the operational
-- DB. Today the variable AIRFLOW_VAR_POSTGRES_CONN is provisioned
-- but no DAG actually consumes it (grep airflow/dags and
-- cartridges/sap_*/dags returns zero hits for PostgresHook /
-- psycopg2). The role is created so that when a DAG does start
-- using it, the blast radius is the operational tables, not super.
-- ─────────────────────────────────────────────────────────────
GRANT CONNECT ON DATABASE modecissions TO omega_airflow_dag;
GRANT USAGE ON SCHEMA public TO omega_airflow_dag;
GRANT SELECT, INSERT, UPDATE ON
    cartridges, cartridge_dags, entity_config,
    entity_watermarks, extraction_runs, pipeline_runs, run_logs,
    datasets, mcp_servers, mcp_custom_tools,
    agents
    TO omega_airflow_dag;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO omega_airflow_dag;
-- (Hard-line REVOKEs already applied in the DO block above.)

-- ─────────────────────────────────────────────────────────────
-- omega_airflow_meta / omega_superset_meta — created here as global
-- roles. Their per-database grants live in the corresponding init
-- containers (airflow-init / superset-init) because:
--
--   1. The DBs ``airflow`` and ``superset`` don't exist yet when
--      ``/docker-entrypoint-initdb.d/*.sql`` runs; the init containers
--      ``CREATE DATABASE`` them on first boot.
--   2. ``GRANT ALL ON ALL TABLES IN SCHEMA public`` must be re-run
--      AFTER ``airflow db migrate`` / ``superset db upgrade`` because
--      those commands CREATE the tables we want to grant on.
--
-- See infra/docker-compose.yml (airflow-init, superset-init blocks)
-- for the per-DB GRANT logic.
-- ─────────────────────────────────────────────────────────────
-- (intentionally no grants here; per-DB grants live in init scripts)
