-- Sprint v1.32: audit plaintext reads from Vault.
--
-- Secrets and connection credentials are decrypted by GET endpoints. Every
-- successful plaintext read must leave an audit trail with the internal caller.

CREATE TABLE IF NOT EXISTS vault_access_log (
    id             BIGSERIAL PRIMARY KEY,
    caller_service TEXT NOT NULL,
    scope          TEXT NOT NULL,
    key            TEXT NOT NULL,
    op             TEXT NOT NULL,
    timestamp      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_vault_access_log_timestamp
    ON vault_access_log(timestamp DESC);

CREATE INDEX IF NOT EXISTS idx_vault_access_log_scope_key
    ON vault_access_log(scope, key);

GRANT SELECT, INSERT ON vault_access_log TO omega_vault;
GRANT USAGE, SELECT ON SEQUENCE vault_access_log_id_seq TO omega_vault;
