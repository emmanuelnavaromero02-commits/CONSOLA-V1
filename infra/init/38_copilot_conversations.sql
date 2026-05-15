-- Sprint v1.41.0 — tornillo para el copiloto IA central (v1.42).
--
-- Estas tablas quedan vacías. La aplicación copiloto que se construirá
-- en sprints v1.42-v1.44 las usará para persistir el historial de chat.
-- Schema preparado para multi-tenant + multi-workspace + tool calls.

CREATE TABLE IF NOT EXISTS conversations (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id         INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    workspace_id    UUID REFERENCES workspaces(id) ON DELETE CASCADE,
    title           TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    archived_at     TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_conversations_user_workspace
    ON conversations(user_id, workspace_id, updated_at DESC);

CREATE TABLE IF NOT EXISTS conversation_messages (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    conversation_id     UUID NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
    role                TEXT NOT NULL CHECK (role IN ('user', 'assistant', 'tool', 'system')),
    content             TEXT,
    tool_calls          JSONB,
    tool_results        JSONB,
    citations           JSONB,
    tokens_input        INTEGER,
    tokens_output       INTEGER,
    model               TEXT,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_messages_conversation
    ON conversation_messages(conversation_id, created_at);

-- Grants for omega_console role (will own these tables operationally).
-- v1.25 already grants ALL on every public table to omega_console; this
-- repeats the grant idempotently so a fresh init can drop migration 25
-- without breaking these tables, and so the intent is documented next to
-- the table definitions.
DO $$
BEGIN
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'omega_console') THEN
    GRANT SELECT, INSERT, UPDATE, DELETE ON conversations TO omega_console;
    GRANT SELECT, INSERT, UPDATE, DELETE ON conversation_messages TO omega_console;
  END IF;
END $$;

-- Defense-in-depth: cartridge / infrastructure roles must not be able to
-- read or write conversation data. Same posture as v1.36/v1.38 used for
-- identity / vault tables.
DO $$
DECLARE
  cartridge_roles CONSTANT text[] := ARRAY[
    'omega_cartridge_sap_hcm', 'omega_cartridge_sap_s4',
    'omega_cartridge_sap_sf', 'omega_cartridge_replicon',
    'omega_mcp_infra', 'omega_airflow_dag'
  ];
  r text;
BEGIN
  FOREACH r IN ARRAY cartridge_roles LOOP
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = r) THEN
      EXECUTE format('REVOKE ALL ON conversations FROM %I', r);
      EXECUTE format('REVOKE ALL ON conversation_messages FROM %I', r);
    END IF;
  END LOOP;
END $$;
