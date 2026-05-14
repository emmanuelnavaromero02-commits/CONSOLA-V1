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
GRANT SELECT ON datasets, entity_config, semantic_terms, pipeline_runs, run_logs,
       cartridges, cartridge_connections, cartridge_dags,
       analytic_apps, silver_lineage, system_settings,
       tenants, workspaces, mcp_servers, mcp_custom_tools
       TO omega_refinement;
-- Write where refinement actually writes.
GRANT INSERT, UPDATE ON datasets, run_logs, pipeline_runs, silver_lineage TO omega_refinement;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO omega_refinement;
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
GRANT SELECT ON datasets, decisions, analytic_apps, semantic_terms,
       users, workspaces, tenants, roles, rag_sources, rag_chunks
       TO omega_workspace;
GRANT INSERT, UPDATE ON decisions, decision_actions TO omega_workspace;
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
-- omega_mcp_infra — actualizado en v1.20.
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
-- La política se relaja a "acceso amplio sobre operativa", PERO
-- vault_entries sigue siendo la línea roja (REVOKE explícito al final).
-- Para credenciales (users.password_hash, user_tokens, refresh_tokens),
-- las tools de mcp-infra no las leen — no hay tool MCP que devuelva
-- password hashes; si en el futuro se agregara, hay que volver a
-- ajustar este bloque.
-- ─────────────────────────────────────────────────────────────
GRANT CONNECT ON DATABASE modecissions TO omega_mcp_infra;
GRANT USAGE ON SCHEMA public TO omega_mcp_infra;
-- Read surface: tablas que las tools de mcp-infra consultan.
GRANT SELECT ON
    cartridges, cartridge_dags, cartridge_connections,
    semantic_terms, mcp_servers, mcp_custom_tools,
    rag_sources, rag_chunks, entity_config, entity_watermarks,
    pipeline_runs, run_logs, datasets, decisions,
    workspaces, tenants, users, roles, system_settings,
    analytic_apps
    TO omega_mcp_infra;
-- Write surface: solo tablas que las tools de mcp-infra escriben hoy.
-- (cartridge_dags y mcp_* via airflow.* tools; rag_* via rag.store.)
GRANT INSERT, UPDATE, DELETE ON
    cartridge_dags, mcp_servers, mcp_custom_tools,
    rag_sources, rag_chunks
    TO omega_mcp_infra;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO omega_mcp_infra;
-- Hard line: NUNCA vault_entries (esa sigue siendo SOLO de omega_vault).
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
