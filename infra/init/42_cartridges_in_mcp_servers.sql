-- Sprint v1.43.1 (Codex P0-3) — Register the 4 cartridges in mcp_servers
-- so the copilot can discover and invoke their tools.
--
-- Idempotent: ON CONFLICT(id) DO UPDATE keeps name/url/category/
-- description fresh if any value drifts but never duplicates the row.
-- tools JSONB stays at '[]' here — it's populated dynamically by
-- console/app/services/mcp_registry.register() which HTTP-fetches
-- /mcp/tools from each cartridge at console startup.

INSERT INTO mcp_servers (id, name, url, category, description, tools, healthy)
VALUES
  ('replicon', 'Replicon Time & Attendance',
   'http://replicon:8201',
   'cartridge',
   'Connector for Replicon workforce management platform.',
   '[]'::jsonb, false),
  ('sap_hcm', 'SAP HCM Core',
   'http://sap-hcm:8202',
   'cartridge',
   'Connector for SAP HCM on-premise / S4HANA HCM.',
   '[]'::jsonb, false),
  ('sap_successfactors', 'SAP SuccessFactors',
   'http://sap-successfactors:8203',
   'cartridge',
   'Connector for SAP SuccessFactors Employee Central.',
   '[]'::jsonb, false),
  ('sap_s4hana', 'SAP S/4HANA',
   'http://sap-s4hana:8204',
   'cartridge',
   'Connector for SAP S/4HANA modules.',
   '[]'::jsonb, false)
ON CONFLICT (id) DO UPDATE SET
  name        = EXCLUDED.name,
  url         = EXCLUDED.url,
  category    = EXCLUDED.category,
  description = EXCLUDED.description;
