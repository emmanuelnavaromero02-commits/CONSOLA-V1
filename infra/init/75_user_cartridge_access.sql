-- User-level cartridge access overrides.
--
-- Tenant/workspace entitlements remain the source of truth for whether a
-- cartridge is licensed. This table lets a platform admin block one user
-- inside that workspace without revoking the whole installation. Removing the
-- row makes the user inherit the workspace entitlement again.

CREATE TABLE IF NOT EXISTS user_cartridge_overrides (
    tenant_id     UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    workspace_id  UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    cartridge_id  TEXT NOT NULL REFERENCES cartridges(id) ON DELETE CASCADE,
    user_id       BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    mode          TEXT NOT NULL,
    reason        TEXT,
    created_by_id BIGINT REFERENCES users(id) ON DELETE SET NULL,
    updated_by_id BIGINT REFERENCES users(id) ON DELETE SET NULL,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (tenant_id, workspace_id, cartridge_id, user_id),
    CONSTRAINT user_cartridge_overrides_mode_chk CHECK (mode IN ('deny'))
);

CREATE INDEX IF NOT EXISTS user_cartridge_overrides_workspace_user_idx
    ON user_cartridge_overrides(workspace_id, user_id);

CREATE INDEX IF NOT EXISTS user_cartridge_overrides_workspace_cartridge_idx
    ON user_cartridge_overrides(workspace_id, cartridge_id, mode);

DELETE FROM user_cartridge_overrides WHERE mode = 'allow';

ALTER TABLE user_cartridge_overrides
    DROP CONSTRAINT IF EXISTS user_cartridge_overrides_mode_chk;

ALTER TABLE user_cartridge_overrides
    ADD CONSTRAINT user_cartridge_overrides_mode_chk CHECK (mode IN ('deny'));

GRANT SELECT, INSERT, UPDATE, DELETE ON user_cartridge_overrides TO omega_console;
GRANT SELECT ON user_cartridge_overrides TO omega_workspace;

INSERT INTO schema_migrations (filename, applied_at)
VALUES ('75_user_cartridge_access.sql', NOW())
ON CONFLICT (filename) DO NOTHING;
