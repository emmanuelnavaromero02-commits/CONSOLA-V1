-- MODecissionsPaaS — decision manager schema
-- Decisions made on top of dashboards/KPIs and their follow-up bitácora.

CREATE TABLE IF NOT EXISTS decisions (
    id                    BIGSERIAL PRIMARY KEY,
    title                 TEXT NOT NULL,
    description           TEXT NOT NULL DEFAULT '',
    created_at            TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    commitment_date       DATE,
    closed_at             TIMESTAMPTZ,
    kpis                  JSONB NOT NULL DEFAULT '[]',
    status                TEXT NOT NULL DEFAULT 'open',   -- open | closed
    outcome               TEXT,                            -- achieved | not_achieved (NULL while open)
    follow_up_decision_id BIGINT REFERENCES decisions(id) ON DELETE SET NULL,
    created_by            TEXT
);

CREATE INDEX IF NOT EXISTS idx_decisions_status
    ON decisions(status, commitment_date);

CREATE TABLE IF NOT EXISTS decision_actions (
    id           BIGSERIAL PRIMARY KEY,
    decision_id  BIGINT NOT NULL REFERENCES decisions(id) ON DELETE CASCADE,
    action_text  TEXT NOT NULL,
    note         TEXT,
    actor        TEXT NOT NULL DEFAULT 'user',
    ts           TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_decision_actions_decision
    ON decision_actions(decision_id, ts DESC);
