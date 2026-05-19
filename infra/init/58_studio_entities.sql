-- v1.44.7 Studio entities registry.
--
-- entity_config remains the operational source used by cartridges and DAGs.
-- studio_entities stores the authoring spec used by Studio so uploaded YAML/JSON
-- can be audited, listed, and replayed without losing the original field model.

CREATE EXTENSION IF NOT EXISTS pgcrypto;

CREATE TABLE IF NOT EXISTS studio_entities (
    id         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name       TEXT NOT NULL,
    cartridge  TEXT NOT NULL,
    spec       JSONB NOT NULL,
    created_by BIGINT REFERENCES users(id),
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(name, cartridge)
);

CREATE INDEX IF NOT EXISTS idx_studio_entities_cartridge
    ON studio_entities(cartridge);

ALTER TABLE IF EXISTS studio_entities
    ALTER COLUMN created_by TYPE BIGINT;

GRANT SELECT, INSERT, UPDATE, DELETE ON studio_entities TO omega_console;

INSERT INTO schema_migrations (filename, applied_at)
VALUES ('58_studio_entities.sql', NOW())
ON CONFLICT (filename) DO NOTHING;
