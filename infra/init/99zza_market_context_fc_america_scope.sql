-- Repair existing installs where 99zz scheduled market jobs in the default
-- tenant/workspace while their Vault connections are scoped to fc america.
WITH market_scope AS (
    SELECT
        'd5d95d5e-0326-4f36-b04f-2a3b77ed61d2'::uuid AS tenant_id,
        '4a6e9743-d54e-46ff-a023-111f06572c42'::uuid AS workspace_id
    WHERE EXISTS (
        SELECT 1 FROM tenants
         WHERE id = 'd5d95d5e-0326-4f36-b04f-2a3b77ed61d2'::uuid
    )
      AND EXISTS (
        SELECT 1 FROM workspaces
         WHERE id = '4a6e9743-d54e-46ff-a023-111f06572c42'::uuid
    )
)
UPDATE entity_config ec
   SET tenant_id = scope.tenant_id,
       workspace_id = scope.workspace_id,
       connection_id = 'default',
       last_scheduled_at = NULL
  FROM market_scope scope
 WHERE ec.cartridge_id IN ('banxico', 'inegi', 'sec_edgar')
   AND ec.entity IN ('series_observations', 'company_facts');

WITH market_scope AS (
    SELECT
        'd5d95d5e-0326-4f36-b04f-2a3b77ed61d2'::uuid AS tenant_id,
        '4a6e9743-d54e-46ff-a023-111f06572c42'::uuid AS workspace_id
    WHERE EXISTS (
        SELECT 1 FROM tenants
         WHERE id = 'd5d95d5e-0326-4f36-b04f-2a3b77ed61d2'::uuid
    )
      AND EXISTS (
        SELECT 1 FROM workspaces
         WHERE id = '4a6e9743-d54e-46ff-a023-111f06572c42'::uuid
    )
)
UPDATE entity_config ec
   SET tenant_id = scope.tenant_id,
       workspace_id = scope.workspace_id,
       last_scheduled_at = NULL
  FROM market_scope scope
 WHERE ec.cartridge_id IN ('banxico', 'inegi', 'sec_edgar')
   AND ec.entity = 'market_context_refresh';

INSERT INTO schema_migrations (filename, applied_at)
VALUES ('99zza_market_context_fc_america_scope.sql', NOW())
ON CONFLICT (filename) DO NOTHING;
