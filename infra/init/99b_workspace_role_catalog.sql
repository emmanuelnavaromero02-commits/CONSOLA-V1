-- v1.45.2 — Reconcile workspace role catalog for upgraded databases.
--
-- Fresh databases get these from 13_rbac_models.sql. Older upgraded volumes can
-- have schema_migrations marking 13 as applied while missing newer workspace
-- roles, which makes tenant IAM silently degrade to viewer/workspace_admin.
--
-- Keep this after 13_rbac_models.sql in lexicographic Docker init order. A
-- plain "101_" prefix sorts before "10_", so this file intentionally uses
-- "99b_".

INSERT INTO roles (name, description)
VALUES
    ('admin', 'Full administrative access within the default workspace on legacy installs'),
    ('workspace_admin', 'Administrative access within an assigned workspace'),
    ('tenant_admin', 'Tenant/workspace account administration without platform-wide admin access'),
    ('analyst', 'Analyze datasets and operational signals within an assigned workspace'),
    ('viewer', 'Read-only access within an assigned workspace'),
    ('workspace_user', 'Basic workspace access without administration')
ON CONFLICT (name) DO UPDATE
SET description = EXCLUDED.description;

INSERT INTO schema_migrations(filename, applied_at)
VALUES ('99b_workspace_role_catalog.sql', NOW())
ON CONFLICT (filename) DO NOTHING;
