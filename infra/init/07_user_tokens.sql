-- MODecissionsPaaS — single-use email tokens for invitation and password reset.

-- Allow users created via invitation flow to start without a password
ALTER TABLE users ALTER COLUMN password_hash DROP NOT NULL;
-- Default new users to inactive — admin-invited users activate via email link
ALTER TABLE users ALTER COLUMN is_active SET DEFAULT FALSE;

CREATE TABLE IF NOT EXISTS user_tokens (
    token       TEXT PRIMARY KEY,                 -- 32-byte hex
    user_id     BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    kind        TEXT NOT NULL,                    -- 'invite' | 'reset'
    expires_at  TIMESTAMPTZ NOT NULL,
    used_at     TIMESTAMPTZ,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_user_tokens_user_kind ON user_tokens(user_id, kind);
CREATE INDEX IF NOT EXISTS idx_user_tokens_expires   ON user_tokens(expires_at);
