-- Agents: configurable assistants that live inside a cartridge.
-- The cartridge is the "mind" (data + hints + knowledge); the agent
-- is a specialization (role + tools + personality + prompt) using
-- the platform as its "body".
--
-- An agent CAN use tools from cartridges other than its own — the
-- cartridge_id just anchors its primary mind + RBAC.

CREATE TABLE IF NOT EXISTS agents (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    cartridge_id    TEXT NOT NULL REFERENCES cartridges(id) ON DELETE CASCADE,
    slug            TEXT NOT NULL,
    name            TEXT NOT NULL,
    description     TEXT NOT NULL DEFAULT '',

    -- Mind components
    instructions    TEXT NOT NULL DEFAULT '',        -- main system prompt
    personality     TEXT NOT NULL DEFAULT '',        -- tone / style / language, appended to instructions
    allowed_tools   JSONB NOT NULL DEFAULT '[]',     -- ["refinement__query_dataset", "mcp-infra__search_rag", ...]
    rag_filter      JSONB NOT NULL DEFAULT '{}',     -- {"cartridges":["replicon"], "kinds":["document","schema"]}
    extra           JSONB NOT NULL DEFAULT '{}',     -- {"variables":{}, "schedule":{"cron":"0 9 * * MON"}}

    -- Model / generation params (per-agent so cost+quality scale with role)
    model           TEXT NOT NULL DEFAULT 'claude-sonnet-4-6',
    max_tokens      INT  NOT NULL DEFAULT 8192,
    temperature     REAL NOT NULL DEFAULT 0.4,

    -- Ownership / lifecycle
    owner_user_id   INT REFERENCES users(id) ON DELETE SET NULL,
    is_active       BOOLEAN NOT NULL DEFAULT TRUE,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    UNIQUE (cartridge_id, slug)
);

CREATE INDEX IF NOT EXISTS agents_cartridge_idx ON agents (cartridge_id);
CREATE INDEX IF NOT EXISTS agents_active_idx    ON agents (is_active) WHERE is_active = TRUE;

-- Conversation log so an agent's runs are inspectable / auditable.
-- Kept lightweight; richer tracing can come later.
CREATE TABLE IF NOT EXISTS agent_runs (
    id              BIGSERIAL PRIMARY KEY,
    agent_id        UUID NOT NULL REFERENCES agents(id) ON DELETE CASCADE,
    user_id         INT  REFERENCES users(id) ON DELETE SET NULL,
    started_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    finished_at     TIMESTAMPTZ,
    status          TEXT NOT NULL DEFAULT 'running',   -- running | ok | error | cancelled
    input_messages  JSONB NOT NULL DEFAULT '[]',
    output_text     TEXT,
    tool_calls      JSONB NOT NULL DEFAULT '[]',       -- list of {tool, args, result_preview}
    tokens_in       INT,
    tokens_out      INT,
    error_message   TEXT
);

CREATE INDEX IF NOT EXISTS agent_runs_agent_idx ON agent_runs (agent_id, started_at DESC);
