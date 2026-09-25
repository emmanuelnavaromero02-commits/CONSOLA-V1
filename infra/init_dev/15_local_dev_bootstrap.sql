-- LOCAL DEV ONLY.
-- Bootstrap a clean docker-compose volume with a usable admin account and
-- workspace mapping. Do not copy these credentials to production.

CREATE EXTENSION IF NOT EXISTS pgcrypto;

INSERT INTO users (email, name, password_hash, role, is_active, must_change_password)
VALUES (
    'emmanuel@local.ai',
    'Local Admin',
    '$2b$12$fL.v2QiBIXa22kx0Vci.ReswZfviXb8uYqSkQFWLzplnkiv.Bu/8i',
    'admin',
    TRUE,
    FALSE
)
ON CONFLICT (email) DO UPDATE
SET name = EXCLUDED.name,
    password_hash = EXCLUDED.password_hash,
    role = EXCLUDED.role,
    is_active = EXCLUDED.is_active,
    must_change_password = EXCLUDED.must_change_password;

UPDATE users u
SET tenant_id = w.tenant_id
FROM workspaces w
JOIN tenants t ON t.id = w.tenant_id
WHERE u.email = 'emmanuel@local.ai'
  AND t.name = 'Default Tenant'
  AND w.name = 'Main Workspace';

INSERT INTO user_workspace_roles (user_id, workspace_id, role_id)
SELECT u.id, w.id, r.id
FROM users u
JOIN workspaces w ON w.name = 'Main Workspace'
JOIN tenants t ON t.id = w.tenant_id AND t.name = 'Default Tenant'
JOIN roles r ON r.name = 'admin'
WHERE u.email = 'emmanuel@local.ai'
ON CONFLICT (user_id, workspace_id, role_id) DO NOTHING;

-- LOCAL DEV ONLY: seed marketplace entitlements + installations so the
-- bootstrap workspace lands with the same cartridges visible as before
-- v1.45.x marketplace gating. Production must never auto-grant cartridges;
-- that is exactly what the marketplace activation flow enforces.
INSERT INTO tenant_entitlements (
    tenant_id, workspace_id, cartridge_id, product_id, status,
    starts_at, created_at, updated_at
)
SELECT
    w.tenant_id,
    w.id,
    c.id,
    mp.id,
    'active',
    NOW(), NOW(), NOW()
  FROM workspaces w
  JOIN tenants t ON t.id = w.tenant_id AND t.name = 'Default Tenant'
  JOIN cartridges c ON TRUE
  JOIN marketplace_products mp ON mp.cartridge_id = c.id
 WHERE w.name = 'Main Workspace'
   AND mp.status IN ('active', 'internal')
ON CONFLICT (tenant_id, workspace_id, cartridge_id) DO UPDATE
  SET product_id = EXCLUDED.product_id,
      status = CASE
          WHEN tenant_entitlements.status IN ('revoked', 'suspended', 'expired')
          THEN tenant_entitlements.status
          ELSE 'active'
      END,
      updated_at = NOW();

INSERT INTO cartridge_installations (
    id, tenant_id, workspace_id, cartridge_id, product_id,
    status, current_step, install_fingerprint, created_at, updated_at, ready_at
)
SELECT
    'devseed_' || md5(w.tenant_id::text || ':' || w.id::text || ':' || c.id),
    w.tenant_id,
    w.id,
    c.id,
    mp.id,
    'ready',
    'seeded_by_local_dev_bootstrap',
    md5(w.tenant_id::text || ':' || w.id::text || ':' || c.id),
    NOW(), NOW(), NOW()
  FROM workspaces w
  JOIN tenants t ON t.id = w.tenant_id AND t.name = 'Default Tenant'
  JOIN cartridges c ON TRUE
  JOIN marketplace_products mp ON mp.cartridge_id = c.id
 WHERE w.name = 'Main Workspace'
   AND mp.status IN ('active', 'internal')
ON CONFLICT (workspace_id, cartridge_id) DO UPDATE
  SET product_id = EXCLUDED.product_id,
      status = CASE
          WHEN cartridge_installations.status IN ('revoked', 'suspended', 'expired')
          THEN cartridge_installations.status
          ELSE 'ready'
      END,
      ready_at = COALESCE(cartridge_installations.ready_at, NOW()),
      updated_at = NOW();

-- ── A1/A2: autonomous SF foundation cycle ─────────────────────────────────────
-- A2: the cycle is managed by cartridge_cycle_config. Seed the dev config row
-- (Default Tenant / Main Workspace, connection 'default', quarter-hourly,
-- foundation target) and re-run the reconciler — it ships in infra/init
-- (99zzzza), which on a fresh install runs before any workspace exists, so
-- its own call no-ops there. After this block a fresh dev install
-- self-schedules exactly like A1 did.
DO $$
BEGIN
    IF to_regclass('public.cartridge_cycle_config') IS NOT NULL THEN
        INSERT INTO public.cartridge_cycle_config
            (tenant_id, workspace_id, cartridge_id, connection_id,
             cron_expression, target, enabled)
        SELECT t.id, w.id, 'sap_successfactors', 'default',
               '*/15 * * * *', 'foundation', TRUE
          FROM public.tenants t
          JOIN public.workspaces w ON w.tenant_id = t.id
         WHERE t.name = 'Default Tenant'
           AND w.name = 'Main Workspace'
           -- Never fight an already-active cycle config (single-active
           -- partial unique index) and never re-enable a deliberately
           -- disabled dev row (ON CONFLICT DO NOTHING below).
           AND NOT EXISTS (
               SELECT 1 FROM public.cartridge_cycle_config c
                WHERE c.cartridge_id = 'sap_successfactors' AND c.enabled
           )
         LIMIT 1
        ON CONFLICT (tenant_id, workspace_id, cartridge_id) DO NOTHING;
    END IF;
    IF to_regproc('public.seed_sap_successfactors_cycle_schedule()') IS NOT NULL THEN
        PERFORM public.seed_sap_successfactors_cycle_schedule();
    END IF;
END
$$;
