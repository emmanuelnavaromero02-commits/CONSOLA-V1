-- MODecissionsPaaS — Audit Events
-- Stores audit trail of user actions

CREATE TABLE IF NOT EXISTS audit_events (
    id            BIGSERIAL PRIMARY KEY,
    user_id       BIGINT,
    email         TEXT,
    action        TEXT NOT NULL,
    resource_type TEXT,
    resource_id   TEXT,
    ip            TEXT,
    user_agent    TEXT,
    status        TEXT,
    metadata      JSONB,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
