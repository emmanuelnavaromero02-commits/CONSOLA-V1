-- v1.45.1 — Workspace session auth needs the user/workspace/role mapping.
--
-- Existing databases created before the v1.45 grant matrix could start the
-- workspace service successfully but fail every authenticated request with:
--
--   permission denied for table user_workspace_roles
--
-- The workspace service reads this table only to construct server-side
-- trusted context (tenant_id, workspace_id, workspace_role). It does not
-- write to the mapping.

GRANT SELECT ON user_workspace_roles TO omega_workspace;

INSERT INTO schema_migrations (filename, applied_at)
VALUES ('72_workspace_rbac_grants.sql', NOW())
ON CONFLICT (filename) DO NOTHING;
