-- Sala de Control OMEGA V1 vendible: impacto, thresholds, acciones seguras y lecciones.
--
-- La V1 prepara execution bridge en modo preview/dry-run. El write-back externo
-- queda bloqueado por feature flag en aplicacion.

ALTER TABLE control_room_items
    ADD COLUMN IF NOT EXISTS impact_estimate NUMERIC(18,2);

ALTER TABLE control_room_items
    ADD COLUMN IF NOT EXISTS impact_currency TEXT NOT NULL DEFAULT 'USD';

ALTER TABLE control_room_items
    ADD COLUMN IF NOT EXISTS confidence NUMERIC(5,2);

ALTER TABLE control_room_items
    ADD COLUMN IF NOT EXISTS priority_score INTEGER NOT NULL DEFAULT 0;

ALTER TABLE control_room_items
    ADD COLUMN IF NOT EXISTS selected_option_id TEXT;

ALTER TABLE control_room_items
    ADD COLUMN IF NOT EXISTS execution_status TEXT NOT NULL DEFAULT 'not_started';

CREATE INDEX IF NOT EXISTS control_room_items_priority_idx
    ON control_room_items(workspace_id, priority_score DESC, severity, last_seen_at DESC);

CREATE INDEX IF NOT EXISTS control_room_items_execution_status_idx
    ON control_room_items(workspace_id, execution_status, last_seen_at DESC);

CREATE TABLE IF NOT EXISTS control_room_action_templates (
    template_id       TEXT PRIMARY KEY,
    cartridge_id      TEXT NOT NULL,
    anomaly_type      TEXT,
    label             TEXT NOT NULL,
    description       TEXT NOT NULL,
    action_kind       TEXT NOT NULL,
    risk_level        TEXT NOT NULL DEFAULT 'medium',
    mode_default      TEXT NOT NULL DEFAULT 'dry_run',
    requires_approval BOOLEAN NOT NULL DEFAULT TRUE,
    enabled           BOOLEAN NOT NULL DEFAULT TRUE,
    config            JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at        TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS control_room_action_templates_lookup_idx
    ON control_room_action_templates(cartridge_id, anomaly_type, enabled);

CREATE TABLE IF NOT EXISTS control_room_action_executions (
    id             BIGSERIAL PRIMARY KEY,
    tenant_id      UUID REFERENCES tenants(id) ON DELETE CASCADE,
    workspace_id   UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    item_id        TEXT NOT NULL,
    template_id    TEXT REFERENCES control_room_action_templates(template_id) ON DELETE SET NULL,
    mode           TEXT NOT NULL,
    status         TEXT NOT NULL,
    payload        JSONB NOT NULL DEFAULT '{}'::jsonb,
    result         JSONB NOT NULL DEFAULT '{}'::jsonb,
    error          TEXT,
    actor_id       BIGINT REFERENCES users(id) ON DELETE SET NULL,
    actor_email    TEXT,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    completed_at   TIMESTAMPTZ,
    FOREIGN KEY (workspace_id, item_id)
      REFERENCES control_room_items(workspace_id, item_id)
      ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS control_room_action_executions_item_idx
    ON control_room_action_executions(workspace_id, item_id, created_at DESC);

CREATE INDEX IF NOT EXISTS control_room_action_executions_status_idx
    ON control_room_action_executions(workspace_id, status, created_at DESC);

CREATE TABLE IF NOT EXISTS control_room_thresholds (
    id             BIGSERIAL PRIMARY KEY,
    tenant_id      UUID REFERENCES tenants(id) ON DELETE CASCADE,
    workspace_id   UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    cartridge_id   TEXT NOT NULL,
    anomaly_type   TEXT NOT NULL,
    metric         TEXT NOT NULL,
    warning_value  NUMERIC(18,4),
    critical_value NUMERIC(18,4),
    currency       TEXT NOT NULL DEFAULT 'USD',
    enabled        BOOLEAN NOT NULL DEFAULT TRUE,
    metadata       JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (workspace_id, cartridge_id, anomaly_type, metric)
);

CREATE INDEX IF NOT EXISTS control_room_thresholds_workspace_idx
    ON control_room_thresholds(workspace_id, cartridge_id, enabled);

CREATE TABLE IF NOT EXISTS control_room_lessons (
    id                 BIGSERIAL PRIMARY KEY,
    tenant_id          UUID REFERENCES tenants(id) ON DELETE CASCADE,
    workspace_id       UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    item_id            TEXT NOT NULL,
    cartridge_id       TEXT NOT NULL,
    anomaly_type       TEXT NOT NULL,
    rule               TEXT NOT NULL,
    source_decision_id BIGINT REFERENCES decisions(id) ON DELETE SET NULL,
    confidence         NUMERIC(5,2) NOT NULL DEFAULT 0.70,
    metadata           JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at         TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS control_room_lessons_pattern_idx
    ON control_room_lessons(workspace_id, cartridge_id, anomaly_type, created_at DESC);

INSERT INTO control_room_action_templates
    (template_id, cartridge_id, anomaly_type, label, description, action_kind, risk_level, mode_default, requires_approval, config)
VALUES
    ('restore_data_source', 'platform', 'source_unavailable', 'Restaurar fuente de datos', 'Valida instalacion, credenciales, materializacion y scope tenant/workspace.', 'data_recovery', 'medium', 'dry_run', TRUE, '{"external_write": false}'::jsonb),
    ('create_followup_task', 'platform', NULL, 'Crear seguimiento operativo', 'Genera una tarea auditada para responsable operativo.', 'followup_task', 'low', 'dry_run', TRUE, '{"external_write": false}'::jsonb),
    ('request_owner_review', 'platform', NULL, 'Solicitar revision de owner', 'Prepara solicitud de revision humana con evidencia y SQL.', 'owner_review', 'low', 'dry_run', TRUE, '{"external_write": false}'::jsonb),
    ('prepare_replicon_adjustment', 'replicon', NULL, 'Preparar ajuste Replicon', 'Construye payload seguro para revisar billing, timesheet o asignacion en Replicon.', 'replicon_adjustment', 'high', 'dry_run', TRUE, '{"target": "replicon", "external_write": false}'::jsonb),
    ('prepare_billing_review', 'replicon', NULL, 'Preparar revision de facturacion', 'Construye evidencia para validar WIP, margen, horas y facturacion.', 'billing_review', 'medium', 'dry_run', TRUE, '{"target": "replicon", "external_write": false}'::jsonb),
    ('prepare_sap_review', 'sap_s4hana', NULL, 'Preparar revision SAP', 'Construye payload de revision para revenue, backlog, compras o maestro S/4.', 'sap_review', 'high', 'dry_run', TRUE, '{"target": "sap_s4hana", "external_write": false}'::jsonb)
ON CONFLICT (template_id) DO UPDATE
SET label = EXCLUDED.label,
    description = EXCLUDED.description,
    action_kind = EXCLUDED.action_kind,
    risk_level = EXCLUDED.risk_level,
    mode_default = EXCLUDED.mode_default,
    requires_approval = EXCLUDED.requires_approval,
    enabled = TRUE,
    config = EXCLUDED.config,
    updated_at = NOW();

INSERT INTO schema_migrations (filename, applied_at)
VALUES ('91_control_room_v1_operational.sql', NOW())
ON CONFLICT (filename) DO NOTHING;
