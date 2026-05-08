-- MODecissionsPaaS - Phase 3.2 RBAC and workspace mapping.
-- This migration is additive and keeps legacy users.role/user_sessions intact.

CREATE EXTENSION IF NOT EXISTS pgcrypto;

CREATE TABLE IF NOT EXISTS tenants (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name        TEXT UNIQUE NOT NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS workspaces (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id   UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    name        TEXT NOT NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_workspaces_tenant_name UNIQUE (tenant_id, name)
);

CREATE TABLE IF NOT EXISTS roles (
    id           SERIAL PRIMARY KEY,
    name         TEXT UNIQUE NOT NULL,
    description  TEXT
);

CREATE TABLE IF NOT EXISTS user_workspace_roles (
    user_id       BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    workspace_id  UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    role_id       INTEGER NOT NULL REFERENCES roles(id) ON DELETE CASCADE,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (user_id, workspace_id, role_id)
);

ALTER TABLE users ADD COLUMN IF NOT EXISTS tenant_id UUID REFERENCES tenants(id) ON DELETE SET NULL;

CREATE INDEX IF NOT EXISTS idx_users_tenant_id ON users(tenant_id);
CREATE INDEX IF NOT EXISTS idx_workspaces_tenant_id ON workspaces(tenant_id);
CREATE INDEX IF NOT EXISTS idx_user_workspace_roles_user_id ON user_workspace_roles(user_id);
CREATE INDEX IF NOT EXISTS idx_user_workspace_roles_workspace_id ON user_workspace_roles(workspace_id);
CREATE INDEX IF NOT EXISTS idx_user_workspace_roles_role_id ON user_workspace_roles(role_id);

INSERT INTO roles (name, description)
VALUES
    ('admin', 'Full administrative access'),
    ('workspace_admin', 'Administrative access within an assigned workspace'),
    ('analyst', 'Create and analyze datasets and decisions within an assigned workspace'),
    ('viewer', 'Read-only access within an assigned workspace')
ON CONFLICT (name) DO UPDATE
SET description = EXCLUDED.description;

INSERT INTO tenants (name)
VALUES ('Default Tenant')
ON CONFLICT (name) DO NOTHING;

INSERT INTO workspaces (tenant_id, name)
SELECT t.id, 'Main Workspace'
FROM tenants t
WHERE t.name = 'Default Tenant'
ON CONFLICT (tenant_id, name) DO NOTHING;

UPDATE users
SET tenant_id = default_tenant.id
FROM (
    SELECT id
    FROM tenants
    WHERE name = 'Default Tenant'
    LIMIT 1
) AS default_tenant
WHERE users.tenant_id IS NULL;

INSERT INTO user_workspace_roles (user_id, workspace_id, role_id)
SELECT u.id, w.id, r.id
FROM users u
CROSS JOIN (
    SELECT w.id
    FROM workspaces w
    JOIN tenants t ON t.id = w.tenant_id
    WHERE t.name = 'Default Tenant'
      AND w.name = 'Main Workspace'
    LIMIT 1
) AS w
JOIN roles r ON r.name = CASE WHEN u.role = 'admin' THEN 'admin' ELSE 'viewer' END
ON CONFLICT (user_id, workspace_id, role_id) DO NOTHING;
