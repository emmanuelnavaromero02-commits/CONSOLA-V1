-- Marketplace status constraint upgrade for existing databases.
--
-- 73_marketplace_installations.sql creates the desired checks for fresh installs,
-- but older local/prod databases may already have the marketplace tables with
-- narrower constraints. This migration widens only the state machines needed by
-- activation, pause, revoke and reactivation.

ALTER TABLE marketplace_orders
    DROP CONSTRAINT IF EXISTS marketplace_orders_status_chk;
ALTER TABLE marketplace_orders
    ADD CONSTRAINT marketplace_orders_status_chk
    CHECK (status IN ('pending', 'approved', 'completed', 'failed', 'cancelled', 'refunded'));

ALTER TABLE tenant_entitlements
    DROP CONSTRAINT IF EXISTS tenant_entitlements_status_chk;
ALTER TABLE tenant_entitlements
    ADD CONSTRAINT tenant_entitlements_status_chk
    CHECK (status IN ('requested', 'active', 'paused', 'revoked', 'expired', 'suspended', 'cancelled'));

ALTER TABLE cartridge_installations
    DROP CONSTRAINT IF EXISTS cartridge_installations_status_chk;
ALTER TABLE cartridge_installations
    ADD CONSTRAINT cartridge_installations_status_chk
    CHECK (status IN (
        'requested', 'installing', 'pending_connection', 'waiting_credentials',
        'ready', 'failed', 'paused', 'revoked', 'expired', 'suspended', 'disabled'
    ));

INSERT INTO schema_migrations (filename, applied_at)
VALUES ('74_marketplace_status_constraints.sql', NOW())
ON CONFLICT (filename) DO NOTHING;
