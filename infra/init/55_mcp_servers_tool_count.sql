-- Sprint v1.44.5 — persist MCP tool_count in the registry.
--
-- Cartridges already expose tool_count from /health, but the central
-- mcp_servers registry only stored the full tools JSONB. Keeping a
-- compact integer lets dashboards and smoke probes query tool health
-- without JSONB expansion.

ALTER TABLE mcp_servers
    ADD COLUMN IF NOT EXISTS tool_count INT NOT NULL DEFAULT 0;

UPDATE mcp_servers
   SET tool_count = COALESCE(jsonb_array_length(tools), 0)
 WHERE tools IS NOT NULL;

INSERT INTO schema_migrations (filename, applied_at)
VALUES ('55_mcp_servers_tool_count.sql', NOW())
ON CONFLICT (filename) DO NOTHING;
