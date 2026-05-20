-- Sprint v1.19 — Postgres roles separados (defense-in-depth, banca).
--
-- Cada servicio se conecta con SU PROPIO ROL con los GRANTs mínimos
-- que necesita. Si UN servicio se compromete (SQL injection, RCE,
-- supply-chain), el atacante NO puede leer/escribir el resto.
--
-- Regla de oro: NINGÚN rol excepto `omega_vault` toca `vault_entries`.
--
-- Las 5 contraseñas se inyectan al server via `PGOPTIONS=-c
-- app.omega_xxx_password=...` definido en infra/docker-compose.yml.
-- libpq las propaga como connection options al backend, así que
-- `current_setting('app.omega_xxx_password', true)` resuelve a la
-- contraseña concreta SOLO durante el init script. Si está vacía,
-- abortamos antes de crear el rol con NULL password (que en otros
-- contextos sería un agujero — un rol con password NULL acepta
-- conexiones sin contraseña).
--
-- IMPORTANTE: este script SOLO corre en una BD recién inicializada.
-- Si los roles ya existen, la rama IF NOT EXISTS los deja como están
-- (con la contraseña original). Para rotar contraseñas en una BD
-- existente, se requiere `ALTER ROLE ... PASSWORD ...` ejecutado por
-- el operador a mano — alcance fuera de este sprint.

-- ─────────────────────────────────────────────────────────────
-- omega_console — owner operacional (users, decisions, jobs, etc)
-- ─────────────────────────────────────────────────────────────
DO $$
DECLARE
  pw TEXT := current_setting('app.omega_console_password', true);
BEGIN
  IF pw IS NULL OR pw = '' THEN
    RAISE EXCEPTION 'app.omega_console_password not set; refusing to create role with empty password';
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'omega_console') THEN
    EXECUTE format('CREATE ROLE omega_console LOGIN PASSWORD %L', pw);
  END IF;
END $$;

GRANT CONNECT ON DATABASE modecissions TO omega_console;
GRANT USAGE ON SCHEMA public TO omega_console;
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO omega_console;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO omega_console;
-- Pero NUNCA vault_entries (ese es solo del rol omega_vault).
REVOKE ALL ON vault_entries FROM omega_console;

-- ─────────────────────────────────────────────────────────────
-- omega_refinement — datasets / pipeline / lineage solamente
-- ─────────────────────────────────────────────────────────────
DO $$
DECLARE
  pw TEXT := current_setting('app.omega_refinement_password', true);
BEGIN
  IF pw IS NULL OR pw = '' THEN
    RAISE EXCEPTION 'app.omega_refinement_password not set; refusing to create role with empty password';
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'omega_refinement') THEN
    EXECUTE format('CREATE ROLE omega_refinement LOGIN PASSWORD %L', pw);
  END IF;
END $$;

GRANT CONNECT ON DATABASE modecissions TO omega_refinement;
GRANT USAGE ON SCHEMA public TO omega_refinement;
-- Read needed for queries (semantic + dataset metadata).
-- v1.20 audit: added data_catalog, data_relationships, kb_config — the
-- refinement engine reads these for semantic linking / dataset graphs
-- and (for catalog tables) writes them back when materializing silver.
GRANT SELECT ON datasets, entity_config, semantic_terms, pipeline_runs, run_logs,
       cartridges, cartridge_connections, cartridge_dags,
       analytic_apps, silver_lineage, system_settings,
       tenants, workspaces, mcp_servers, mcp_custom_tools,
       data_catalog, data_relationships, kb_config
       TO omega_refinement;
-- Write where refinement actually writes.
-- v1.20 audit: added data_catalog, data_relationships (re-seeded on
-- refresh) and analytic_apps (datasets_used column auto-extracted).
GRANT INSERT, UPDATE ON datasets, run_logs, pipeline_runs, silver_lineage,
       data_catalog, data_relationships, analytic_apps
       TO omega_refinement;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO omega_refinement;
GRANT USAGE, SELECT ON SEQUENCE data_catalog_id_seq TO omega_refinement;
GRANT USAGE, SELECT ON SEQUENCE data_relationships_id_seq TO omega_refinement;
-- Refinement NUNCA debe tocar:
--   vault_entries          (secretos)
--   users                  (password hashes)
--   user_sessions, user_tokens, refresh_tokens (auth state)
-- El REVOKE explícito de vault_entries es defense-in-depth contra un
-- futuro operador que agregue `GRANT ... ON ALL TABLES` arriba sin
-- recordar excluir esta tabla.
REVOKE ALL ON vault_entries FROM omega_refinement;

-- ─────────────────────────────────────────────────────────────
-- omega_vault — vault_entries y nada más
-- ─────────────────────────────────────────────────────────────
DO $$
DECLARE
  pw TEXT := current_setting('app.omega_vault_password', true);
BEGIN
  IF pw IS NULL OR pw = '' THEN
    RAISE EXCEPTION 'app.omega_vault_password not set; refusing to create role with empty password';
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'omega_vault') THEN
    EXECUTE format('CREATE ROLE omega_vault LOGIN PASSWORD %L', pw);
  END IF;
END $$;

GRANT CONNECT ON DATABASE modecissions TO omega_vault;
GRANT USAGE ON SCHEMA public TO omega_vault;
GRANT SELECT, INSERT, UPDATE, DELETE ON vault_entries TO omega_vault;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO omega_vault;

-- ─────────────────────────────────────────────────────────────
-- omega_workspace — read-most + write decisions/decision_actions
-- ─────────────────────────────────────────────────────────────
DO $$
DECLARE
  pw TEXT := current_setting('app.omega_workspace_password', true);
BEGIN
  IF pw IS NULL OR pw = '' THEN
    RAISE EXCEPTION 'app.omega_workspace_password not set; refusing to create role with empty password';
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'omega_workspace') THEN
    EXECUTE format('CREATE ROLE omega_workspace LOGIN PASSWORD %L', pw);
  END IF;
END $$;

GRANT CONNECT ON DATABASE modecissions TO omega_workspace;
GRANT USAGE ON SCHEMA public TO omega_workspace;
-- v1.20 audit: added user_sessions + token_usage. Workspace reads the
-- session cookie to identify the caller (user_sessions) and emits per-
-- request token usage rows (token_usage) for the LLM cost dashboard.
GRANT SELECT ON datasets, decisions, analytic_apps, semantic_terms,
       users, workspaces, tenants, roles, rag_sources, rag_chunks,
       user_sessions, token_usage
       TO omega_workspace;
GRANT INSERT, UPDATE ON decisions, decision_actions,
       user_sessions, token_usage
       TO omega_workspace;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO omega_workspace;
REVOKE ALL ON vault_entries FROM omega_workspace;

-- ─────────────────────────────────────────────────────────────
-- omega_mcp_infra — mcp_servers / mcp_custom_tools solamente
-- ─────────────────────────────────────────────────────────────
DO $$
DECLARE
  pw TEXT := current_setting('app.omega_mcp_infra_password', true);
BEGIN
  IF pw IS NULL OR pw = '' THEN
    RAISE EXCEPTION 'app.omega_mcp_infra_password not set; refusing to create role with empty password';
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'omega_mcp_infra') THEN
    EXECUTE format('CREATE ROLE omega_mcp_infra LOGIN PASSWORD %L', pw);
  END IF;
END $$;

-- ─────────────────────────────────────────────────────────────
-- omega_mcp_infra — actualizado en v1.20, endurecido en v1.36.
--
-- mcp-infra es deliberadamente un "router" que expone tools MCP
-- que tocan muchas tablas operacionales: pipeline.dag_get_source
-- lee de cartridge_dags, airflow.* hace INSERT/UPDATE/DELETE en
-- cartridge_dags, cartridges.* lee de semantic_terms / data_catalog
-- / cartridges, postgres.* corre queries genéricas sobre operativa,
-- rag.store lee y escribe rag_sources / rag_chunks, etc. Restringirlo
-- a 2 tablas (como hacía v1.19) rompe Studio paso 2 — "Fuente no
-- encontrada en BD" — porque mcp-infra no puede SELECT cartridge_dags.
--
-- Sprint v1.36 (audit B4 P0): identity / auth / decisions / RBAC
-- tablas NO están en el GRANT. mcp-infra no las consulta hoy (grep
-- "FROM users|tenants|decisions|roles|workspaces" en mcp-infra/app
-- retorna 0 hits) y nunca debería: aunque cada tool individual no
-- las devuelva, ``postgres_execute_query`` permite SELECT arbitrario
-- y el GRANT le daría a un caller no-admin (en una versión futura
-- con bug de auth) un camino a ``users.password_hash``. La línea
-- roja sigue siendo ``vault_entries`` (REVOKE al final); v1.36 añade
-- el mismo trato defense-in-depth para las tablas de identity en la
-- migración ``infra/init/35_omega_mcp_infra_lockdown.sql``.
-- ─────────────────────────────────────────────────────────────
GRANT CONNECT ON DATABASE modecissions TO omega_mcp_infra;
GRANT USAGE ON SCHEMA public TO omega_mcp_infra;
-- Read surface: tablas OPERATIVAS que las tools de mcp-infra
-- consultan. v1.20 audit: added data_catalog + kb_config —
-- cartridges.* tools read them for the catalog/KB graph rendered
-- in Studio. v1.36 audit: removed decisions / workspaces / tenants
-- / users / roles — no tool reads them and they were the path to
-- password hash exfiltration if combined with a SELECT-arbitrary
-- bug.
GRANT SELECT ON
    cartridges, cartridge_dags, cartridge_connections,
    semantic_terms, mcp_servers, mcp_custom_tools,
    rag_sources, rag_chunks, entity_config, entity_watermarks,
    pipeline_runs, run_logs, datasets, system_settings,
    analytic_apps, data_catalog, kb_config, agents
    TO omega_mcp_infra;
-- Write surface: solo tablas que las tools de mcp-infra escriben hoy.
-- (cartridge_dags y mcp_* via airflow.* tools; rag_* via rag.store.
-- v1.20 audit: added entity_watermarks + pipeline_runs — the airflow
-- tools advance watermarks and write pipeline run rows from DAG callbacks.)
GRANT INSERT, UPDATE, DELETE ON
    cartridge_dags, mcp_servers, mcp_custom_tools,
    rag_sources, rag_chunks, entity_watermarks, pipeline_runs, agents
    TO omega_mcp_infra;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO omega_mcp_infra;
-- Hard lines (defense-in-depth):
--   * vault_entries siempre fue SOLO de omega_vault (regla v1.19).
--   * v1.36 (audit B4): identity / auth / decisions / RBAC tablas
--     también son hard line para mcp-infra. La migración
--     ``35_omega_mcp_infra_lockdown.sql`` agrega los REVOKE
--     explícitos para deployments preexistentes donde el GRANT
--     anterior ya quedó en pg_catalog.
REVOKE ALL ON vault_entries FROM omega_mcp_infra;

-- ─────────────────────────────────────────────────────────────
-- Future-proofing: cualquier tabla nueva que cree el superuser
-- `postgres` hereda los grants para omega_console (el rol más
-- amplio). Otros roles siguen restringidos a sus tablas concretas.
-- ─────────────────────────────────────────────────────────────
ALTER DEFAULT PRIVILEGES IN SCHEMA public
   GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO omega_console;
ALTER DEFAULT PRIVILEGES IN SCHEMA public
   GRANT USAGE, SELECT ON SEQUENCES TO omega_console;
