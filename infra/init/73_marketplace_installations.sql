-- Marketplace / cartridge activation control plane.
--
-- This layer turns the global cartridge catalog into explicit tenant/workspace
-- access. A purchase creates an order, an entitlement and an idempotent
-- installation row. Studio remains the internal operator surface; Workspace and
-- Copilot read entitlements/installations to decide what a customer can see.

CREATE TABLE IF NOT EXISTS marketplace_products (
    id           TEXT PRIMARY KEY,
    cartridge_id TEXT NOT NULL UNIQUE REFERENCES cartridges(id) ON DELETE CASCADE,
    name         TEXT NOT NULL,
    description  TEXT,
    category     TEXT,
    status       TEXT NOT NULL DEFAULT 'active',
    sort_order   INTEGER NOT NULL DEFAULT 100,
    metadata     JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT marketplace_products_status_chk
      CHECK (status IN ('active', 'internal', 'disabled', 'archived'))
);

CREATE TABLE IF NOT EXISTS marketplace_orders (
    id              TEXT PRIMARY KEY,
    tenant_id       UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    workspace_id    UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    product_id      TEXT NOT NULL REFERENCES marketplace_products(id) ON DELETE RESTRICT,
    cartridge_id    TEXT NOT NULL REFERENCES cartridges(id) ON DELETE RESTRICT,
    status          TEXT NOT NULL DEFAULT 'completed',
    source          TEXT NOT NULL DEFAULT 'console',
    idempotency_key TEXT NOT NULL,
    created_by_id   BIGINT REFERENCES users(id) ON DELETE SET NULL,
    metadata        JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT marketplace_orders_status_chk
      CHECK (status IN ('pending', 'approved', 'completed', 'failed', 'cancelled', 'refunded'))
);

CREATE UNIQUE INDEX IF NOT EXISTS marketplace_orders_idempotent_uniq
    ON marketplace_orders(workspace_id, product_id, idempotency_key);

CREATE TABLE IF NOT EXISTS tenant_entitlements (
    tenant_id       UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    workspace_id    UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    cartridge_id    TEXT NOT NULL REFERENCES cartridges(id) ON DELETE CASCADE,
    product_id      TEXT NOT NULL REFERENCES marketplace_products(id) ON DELETE RESTRICT,
    status          TEXT NOT NULL DEFAULT 'active',
    source_order_id TEXT REFERENCES marketplace_orders(id) ON DELETE SET NULL,
    activated_by_id BIGINT REFERENCES users(id) ON DELETE SET NULL,
    starts_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    ends_at         TIMESTAMPTZ,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (tenant_id, workspace_id, cartridge_id),
    CONSTRAINT tenant_entitlements_status_chk
      CHECK (status IN ('requested', 'active', 'paused', 'revoked', 'expired', 'suspended', 'cancelled'))
);

CREATE INDEX IF NOT EXISTS tenant_entitlements_workspace_idx
    ON tenant_entitlements(workspace_id, status, cartridge_id);

CREATE TABLE IF NOT EXISTS cartridge_installations (
    id                  TEXT PRIMARY KEY,
    tenant_id           UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    workspace_id        UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    cartridge_id        TEXT NOT NULL REFERENCES cartridges(id) ON DELETE CASCADE,
    product_id          TEXT NOT NULL REFERENCES marketplace_products(id) ON DELETE RESTRICT,
    order_id            TEXT REFERENCES marketplace_orders(id) ON DELETE SET NULL,
    status              TEXT NOT NULL DEFAULT 'ready',
    current_step        TEXT NOT NULL DEFAULT 'activated',
    error_message       TEXT,
    install_fingerprint TEXT NOT NULL,
    created_by_id       BIGINT REFERENCES users(id) ON DELETE SET NULL,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    ready_at            TIMESTAMPTZ,
    CONSTRAINT cartridge_installations_status_chk
      CHECK (status IN (
          'requested', 'installing', 'pending_connection', 'waiting_credentials',
          'ready', 'failed', 'paused', 'revoked', 'expired', 'suspended', 'disabled'
      ))
);

CREATE UNIQUE INDEX IF NOT EXISTS cartridge_installations_workspace_cartridge_uniq
    ON cartridge_installations(workspace_id, cartridge_id);
CREATE UNIQUE INDEX IF NOT EXISTS cartridge_installations_tenant_workspace_cartridge_uniq
    ON cartridge_installations(tenant_id, workspace_id, cartridge_id);

CREATE TABLE IF NOT EXISTS cartridge_installation_events (
    id              BIGSERIAL PRIMARY KEY,
    installation_id TEXT NOT NULL REFERENCES cartridge_installations(id) ON DELETE CASCADE,
    step            TEXT NOT NULL,
    status          TEXT NOT NULL,
    message         TEXT,
    metadata        JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS cartridge_installation_events_installation_idx
    ON cartridge_installation_events(installation_id, created_at DESC);

INSERT INTO marketplace_products (id, cartridge_id, name, description, category, status, metadata)
SELECT
    c.id,
    c.id,
    c.name,
    c.description,
    COALESCE(NULLIF(c.category, ''), 'connector'),
    CASE WHEN c.id = 'platform' THEN 'internal' ELSE 'active' END,
    jsonb_build_object(
      'version', COALESCE(c.version, ''),
      'pattern', COALESCE(c.pattern, ''),
      'bronze_path', COALESCE(c.bronze_path, '')
    )
FROM cartridges c
ON CONFLICT (id) DO UPDATE
SET name = EXCLUDED.name,
    cartridge_id = EXCLUDED.cartridge_id,
    description = EXCLUDED.description,
    category = EXCLUDED.category,
    metadata = marketplace_products.metadata || EXCLUDED.metadata,
    updated_at = NOW();

-- Fresh installs and upgrades must not accidentally hide pre-existing
-- cartridge data when the entitlement tables appear for the first time. If the
-- datasets table already carries workspace scope, backfill an active
-- entitlement/ready installation for each workspace/cartridge pair already in
-- use. Explicitly blocked states are preserved.
DO $$
BEGIN
  IF EXISTS (
      SELECT 1
        FROM information_schema.columns
       WHERE table_schema = 'public'
         AND table_name = 'datasets'
         AND column_name = 'workspace_id'
  ) THEN
    INSERT INTO tenant_entitlements (
        tenant_id, workspace_id, cartridge_id, product_id, status,
        starts_at, created_at, updated_at
    )
    SELECT DISTINCT
        w.tenant_id,
        d.workspace_id,
        d.cartridge,
        mp.id,
        'active',
        NOW(),
        NOW(),
        NOW()
      FROM datasets d
      JOIN workspaces w ON w.id = d.workspace_id
      JOIN marketplace_products mp ON mp.cartridge_id = d.cartridge
     WHERE d.workspace_id IS NOT NULL
       AND COALESCE(d.cartridge, '') <> ''
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
    SELECT DISTINCT
        'seed_' || md5(w.tenant_id::text || ':' || d.workspace_id::text || ':' || d.cartridge),
        w.tenant_id,
        d.workspace_id,
        d.cartridge,
        mp.id,
        'ready',
        'seeded_from_existing_dataset',
        md5(w.tenant_id::text || ':' || d.workspace_id::text || ':' || d.cartridge),
        NOW(),
        NOW(),
        NOW()
      FROM datasets d
      JOIN workspaces w ON w.id = d.workspace_id
      JOIN marketplace_products mp ON mp.cartridge_id = d.cartridge
     WHERE d.workspace_id IS NOT NULL
       AND COALESCE(d.cartridge, '') <> ''
       AND mp.status IN ('active', 'internal')
    ON CONFLICT (workspace_id, cartridge_id) DO UPDATE
      SET product_id = EXCLUDED.product_id,
          status = CASE
              WHEN cartridge_installations.status IN ('revoked', 'suspended', 'expired')
              THEN cartridge_installations.status
              ELSE 'ready'
          END,
          current_step = CASE
              WHEN cartridge_installations.status IN ('revoked', 'suspended', 'expired')
              THEN cartridge_installations.current_step
              ELSE 'seeded_from_existing_dataset'
          END,
          ready_at = COALESCE(cartridge_installations.ready_at, NOW()),
          updated_at = NOW();
  END IF;
END $$;

GRANT SELECT, INSERT, UPDATE, DELETE ON marketplace_products,
    marketplace_orders, tenant_entitlements, cartridge_installations,
    cartridge_installation_events
    TO omega_console;
GRANT USAGE, SELECT ON SEQUENCE cartridge_installation_events_id_seq TO omega_console;

GRANT SELECT ON marketplace_products, tenant_entitlements, cartridge_installations
    TO omega_workspace;

INSERT INTO schema_migrations (filename, applied_at)
VALUES ('73_marketplace_installations.sql', NOW())
ON CONFLICT (filename) DO NOTHING;
