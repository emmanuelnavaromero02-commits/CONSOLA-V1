-- MODecissionsPaaS — Schema migrations tracking (Fase 5)
-- Registro de las migraciones SQL aplicadas. Backfill idempotente con las
-- migraciones existentes hasta esta fase.

CREATE TABLE IF NOT EXISTS schema_migrations (
    id            BIGSERIAL PRIMARY KEY,
    filename      TEXT NOT NULL UNIQUE,
    applied_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    checksum      TEXT
);

INSERT INTO schema_migrations (filename, applied_at) VALUES
  ('00_schema.sql', NOW()),
  ('02_replicon_seed.sql', NOW()),
  ('03_pgvector_rag.sql', NOW()),
  ('04_decisions.sql', NOW()),
  ('05_users.sql', NOW()),
  ('06_password_change.sql', NOW()),
  ('07_user_tokens.sql', NOW()),
  ('08_workspace_ownership.sql', NOW()),
  ('09_bucket_template.sql', NOW()),
  ('10_replicon_gold_seed.sql', NOW()),
  ('11_app_datasets_used.sql', NOW()),
  ('12_refresh_tokens.sql', NOW()),
  ('13_rbac_models.sql', NOW()),
  ('14_token_usage_cache_columns.sql', NOW()),
  ('15_local_dev_bootstrap.sql', NOW()),
  ('16_audit_events.sql', NOW()),
  ('17_login_security.sql', NOW()),
  ('18_sessions_extended.sql', NOW()),
  ('19_operational_stability_hotfix.sql', NOW()),
  ('20_sap_cartridges_seed.sql', NOW()),
  ('21_system_settings.sql', NOW()),
  ('22_schema_migrations.sql', NOW())
ON CONFLICT (filename) DO NOTHING;
