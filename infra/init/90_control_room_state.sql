-- Sala de Control OMEGA: persistent state for operational items.
--
-- Raw anomalies remain in the source datasets. These tables store the user's
-- workflow state: decisions, approvals, dismissals and learned events per
-- workspace-safe item id.

CREATE TABLE IF NOT EXISTS control_room_items (
    tenant_id       UUID REFERENCES tenants(id) ON DELETE CASCADE,
    workspace_id    UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    item_id         TEXT NOT NULL,
    cartridge_id    TEXT NOT NULL REFERENCES cartridges(id) ON DELETE CASCADE,
    domain          TEXT NOT NULL,
    source_dataset  TEXT,
    item_kind       TEXT NOT NULL DEFAULT 'anomaly',
    title           TEXT NOT NULL,
    severity        TEXT NOT NULL DEFAULT 'medium',
    status          TEXT NOT NULL DEFAULT 'open',
    decision_id     BIGINT REFERENCES decisions(id) ON DELETE SET NULL,
    entity_kind     TEXT,
    entity_id       TEXT,
    entity_label    TEXT,
    anomaly_type    TEXT,
    metadata        JSONB NOT NULL DEFAULT '{}'::jsonb,
    first_seen_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_seen_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    resolved_at     TIMESTAMPTZ,
    dismissed_at    TIMESTAMPTZ,
    PRIMARY KEY (workspace_id, item_id),
    CONSTRAINT control_room_items_status_chk
      CHECK (status IN ('open', 'in_review', 'decision_created', 'approved', 'dismissed', 'resolved')),
    CONSTRAINT control_room_items_severity_chk
      CHECK (severity IN ('critical', 'high', 'medium', 'low'))
);

CREATE INDEX IF NOT EXISTS control_room_items_workspace_status_idx
    ON control_room_items(workspace_id, status, severity, last_seen_at DESC);

CREATE INDEX IF NOT EXISTS control_room_items_workspace_cartridge_idx
    ON control_room_items(workspace_id, cartridge_id, status);

CREATE INDEX IF NOT EXISTS control_room_items_decision_idx
    ON control_room_items(decision_id)
    WHERE decision_id IS NOT NULL;

CREATE TABLE IF NOT EXISTS control_room_item_events (
    id            BIGSERIAL PRIMARY KEY,
    tenant_id     UUID REFERENCES tenants(id) ON DELETE CASCADE,
    workspace_id  UUID NOT NULL,
    item_id       TEXT NOT NULL,
    event_type    TEXT NOT NULL,
    actor_id      BIGINT REFERENCES users(id) ON DELETE SET NULL,
    actor_email   TEXT,
    metadata      JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    FOREIGN KEY (workspace_id, item_id)
      REFERENCES control_room_items(workspace_id, item_id)
      ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS control_room_item_events_item_idx
    ON control_room_item_events(workspace_id, item_id, created_at DESC);

INSERT INTO schema_migrations (filename, applied_at)
VALUES ('90_control_room_state.sql', NOW())
ON CONFLICT (filename) DO NOTHING;
