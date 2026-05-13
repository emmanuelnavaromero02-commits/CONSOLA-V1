-- Sprint v1.15 — encryption at rest for vault_entries
--
-- Adds a BYTEA column for Fernet-encrypted secrets. The existing
-- `value` JSONB column is kept temporarily for backward compatibility
-- during the rollout (reads fall back to it when value_encrypted IS NULL).
--
-- Once all rows are migrated and verified, a future migration can DROP
-- the legacy `value` column. Until then, vault_entries.value is allowed
-- to be NULL on rows whose secret has already been encrypted.

ALTER TABLE vault_entries
    ADD COLUMN IF NOT EXISTS value_encrypted BYTEA;

-- value becomes nullable since rows after migration will use value_encrypted
ALTER TABLE vault_entries
    ALTER COLUMN value DROP NOT NULL;

-- Helps the runtime migration's "find still-plaintext rows" query.
CREATE INDEX IF NOT EXISTS idx_vault_entries_has_encrypted
    ON vault_entries ((value_encrypted IS NOT NULL));
