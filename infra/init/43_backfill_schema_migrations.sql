-- Sprint v1.43.1 (Codex P1-1) — Backfill schema_migrations for files
-- that ran via docker-entrypoint-initdb.d on fresh installs but never
-- went through scripts/apply_db_migrations.sh (which is what stamps
-- the row in schema_migrations).
--
-- Fresh installs only run the entrypoint init scripts once and skip
-- the runner entirely. Upgrades run the runner on existing volumes
-- but the runner used to stop registering at 22_schema_migrations.sql
-- (the migration that created the tracking table itself). Result: any
-- environment that's been upgraded a few times has migrations 23-42
-- physically applied but never tracked in schema_migrations, making
-- "which migration ran last?" impossible to answer reliably.
--
-- Idempotent: ON CONFLICT (filename) DO NOTHING is the same posture
-- the runner uses, so re-running this is a no-op.

CREATE TABLE IF NOT EXISTS schema_migrations (
    id          BIGSERIAL PRIMARY KEY,
    filename    TEXT NOT NULL UNIQUE,
    applied_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    checksum    TEXT
);

INSERT INTO schema_migrations (filename, applied_at)
SELECT m.filename, NOW()
FROM (VALUES
    ('00_schema.sql'),
    ('02_replicon_seed.sql'),
    ('03_pgvector_rag.sql'),
    ('04_decisions.sql'),
    ('05_users.sql'),
    ('06_password_change.sql'),
    ('07_user_tokens.sql'),
    ('08_workspace_ownership.sql'),
    ('09_bucket_template.sql'),
    ('10_replicon_gold_seed.sql'),
    ('11_app_datasets_used.sql'),
    ('12_refresh_tokens.sql'),
    ('13_rbac_models.sql'),
    ('14_token_usage_cache_columns.sql'),
    ('15_local_dev_bootstrap.sql'),
    ('16_audit_events.sql'),
    ('17_login_security.sql'),
    ('18_sessions_extended.sql'),
    ('19_operational_stability_hotfix.sql'),
    ('20_sap_cartridges_seed.sql'),
    ('21_system_settings.sql'),
    ('22_schema_migrations.sql'),
    ('23_datasets_workspace_id.sql'),
    ('24_vault_encryption.sql'),
    ('25_service_roles.sql'),
    ('31_entity_config_sap_columns.sql'),
    ('32_vault_audit_log.sql'),
    ('33_decisions_workspace_id.sql'),
    ('34_postgres_gold_role.sql'),
    ('35_omega_mcp_infra_lockdown.sql'),
    ('36_cartridge_and_meta_roles.sql'),
    ('37_replicon_role_and_tables.sql'),
    ('38_copilot_conversations.sql'),
    ('39_audit_tool_columns.sql'),
    ('40_v141_observability_indexes.sql'),
    ('41_copilot_query_indexes.sql'),
    ('42_cartridges_in_mcp_servers.sql')
) AS m(filename)
ON CONFLICT (filename) DO NOTHING;

-- This file itself: register it here too (the runner won't see it
-- because docker entrypoint runs it before the runner is invoked).
INSERT INTO schema_migrations (filename, applied_at)
VALUES ('43_backfill_schema_migrations.sql', NOW())
ON CONFLICT (filename) DO NOTHING;
