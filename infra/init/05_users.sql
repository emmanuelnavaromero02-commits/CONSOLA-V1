-- MODecissionsPaaS — local user management & sessions
-- Initial security model. SSO (Entra ID) lands later as a parallel login path.

CREATE TABLE IF NOT EXISTS users (
    id            BIGSERIAL PRIMARY KEY,
    email         TEXT UNIQUE NOT NULL,
    name          TEXT,
    password_hash TEXT NOT NULL,
    role          TEXT NOT NULL DEFAULT 'user',   -- user | admin
    is_active     BOOLEAN NOT NULL DEFAULT TRUE,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_login    TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS user_sessions (
    token       TEXT PRIMARY KEY,                -- random 32 bytes hex
    user_id     BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    expires_at  TIMESTAMPTZ NOT NULL,
    ip          TEXT
);

CREATE INDEX IF NOT EXISTS idx_user_sessions_user_id  ON user_sessions(user_id);
CREATE INDEX IF NOT EXISTS idx_user_sessions_expires  ON user_sessions(expires_at);

-- Decision ownership + visibility (idempotent for existing installs)
ALTER TABLE decisions ADD COLUMN IF NOT EXISTS created_by_id BIGINT REFERENCES users(id) ON DELETE SET NULL;
ALTER TABLE decisions ADD COLUMN IF NOT EXISTS assignee_id   BIGINT REFERENCES users(id) ON DELETE SET NULL;
ALTER TABLE decisions ADD COLUMN IF NOT EXISTS visibility    TEXT NOT NULL DEFAULT 'private';

CREATE INDEX IF NOT EXISTS idx_decisions_owner    ON decisions(created_by_id);
CREATE INDEX IF NOT EXISTS idx_decisions_assignee ON decisions(assignee_id);
