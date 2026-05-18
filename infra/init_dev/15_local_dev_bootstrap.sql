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

UPDATE cartridges
SET name = 'Replicon PSA',
    version = '3.0.0',
    description = 'Replicon Professional Services Automation — extrae datos de workforce: usuarios, proyectos, tiempo registrado, tareas, clientes, facturas, asignaciones y gastos.',
    pattern = 'dag-based',
    category = 'cartridge',
    bronze_path = 'raw/replicon/{entity}/load_date={date}/',
    updated_at = NOW()
WHERE id = 'replicon';
