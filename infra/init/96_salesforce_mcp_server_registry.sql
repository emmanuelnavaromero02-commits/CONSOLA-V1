-- v1.44.6: Salesforce was added to the cartridge stack after some
-- environments had already applied 42_cartridges_in_mcp_servers.sql.
-- Keep this as a backfill so upgraded databases register the service too.
INSERT INTO mcp_servers (id, name, url, category, description, tools, healthy)
VALUES (
  'salesforce',
  'Salesforce Sales Cloud',
  'http://salesforce:8205',
  'cartridge',
  'Connector for Salesforce Sales Cloud.',
  '[]'::jsonb,
  false
)
ON CONFLICT (id) DO UPDATE SET
  name        = EXCLUDED.name,
  url         = EXCLUDED.url,
  category    = EXCLUDED.category,
  description = EXCLUDED.description;
