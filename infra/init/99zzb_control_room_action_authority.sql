-- P1.2c3 PR-A: durable authority for one internal Control Room action.
-- Public approval/execution routes are intentionally outside this migration.

CREATE UNIQUE INDEX IF NOT EXISTS workspaces_tenant_id_id_idx
    ON workspaces(tenant_id, id);

INSERT INTO roles (name, description)
VALUES (
    'control_room_approver',
    'Maker-separated approver for internal Control Room actions'
)
ON CONFLICT (name) DO UPDATE SET description = EXCLUDED.description;

CREATE TABLE IF NOT EXISTS control_room_action_intents (
    id                       UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id                UUID NOT NULL,
    workspace_id             UUID NOT NULL,
    item_id                  TEXT NOT NULL,
    maker_user_id            BIGINT NOT NULL,
    checker_user_id          BIGINT,
    executor_user_id         BIGINT,
    template_id              TEXT NOT NULL,
    binding_digest           CHAR(64) NOT NULL,
    evidence_digest          CHAR(64) NOT NULL,
    observation_fingerprint  CHAR(64) NOT NULL,
    contract_digest          CHAR(64) NOT NULL,
    target_digest            CHAR(64) NOT NULL,
    dry_run_digest           CHAR(64) NOT NULL,
    decision_digest          CHAR(64) NOT NULL,
    state                    TEXT NOT NULL DEFAULT 'pending_approval',
    state_version            INTEGER NOT NULL DEFAULT 1,
    result_code              TEXT NOT NULL DEFAULT 'created',
    correlation_id           UUID NOT NULL DEFAULT gen_random_uuid(),
    created_at               TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at               TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    expires_at               TIMESTAMPTZ NOT NULL,
    UNIQUE (tenant_id, workspace_id, id),
    UNIQUE (workspace_id, binding_digest),
    FOREIGN KEY (tenant_id, workspace_id)
      REFERENCES workspaces(tenant_id, id) ON DELETE RESTRICT,
    FOREIGN KEY (workspace_id, item_id)
      REFERENCES control_room_items(workspace_id, item_id) ON DELETE RESTRICT,
    CONSTRAINT control_room_action_intents_template_chk
      CHECK (template_id = 'create_followup_task'),
    CONSTRAINT control_room_action_intents_state_chk
      CHECK (state IN (
        'pending_approval', 'approved', 'rejected', 'stale',
        'execution_reserved', 'completed', 'failed'
      )),
    CONSTRAINT control_room_action_intents_result_chk
      CHECK (result_code IN (
        'created', 'claimed', 'approved', 'rejected', 'stale',
        'execution_reserved', 'completed', 'failed'
      )),
    CONSTRAINT control_room_action_intents_version_chk
      CHECK (state_version >= 1),
    CONSTRAINT control_room_action_intents_checker_chk
      CHECK (checker_user_id IS NULL OR checker_user_id <> maker_user_id),
    CONSTRAINT control_room_action_intents_executor_chk
      CHECK (executor_user_id IS NULL OR executor_user_id = checker_user_id),
    CONSTRAINT control_room_action_intents_expiry_chk
      CHECK (
        expires_at > created_at
        AND expires_at <= created_at + INTERVAL '24 hours'
      ),
    CONSTRAINT control_room_action_intents_digests_chk CHECK (
      binding_digest ~ '^[0-9a-f]{64}$'
      AND evidence_digest ~ '^[0-9a-f]{64}$'
      AND observation_fingerprint ~ '^[0-9a-f]{64}$'
      AND contract_digest ~ '^[0-9a-f]{64}$'
      AND target_digest ~ '^[0-9a-f]{64}$'
      AND dry_run_digest ~ '^[0-9a-f]{64}$'
      AND decision_digest ~ '^[0-9a-f]{64}$'
    )
);

CREATE INDEX IF NOT EXISTS control_room_action_intents_state_idx
    ON control_room_action_intents(workspace_id, state, expires_at);

CREATE TABLE IF NOT EXISTS control_room_action_tokens (
    id                       UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id                UUID NOT NULL,
    workspace_id             UUID NOT NULL,
    intent_id                UUID,
    stage                    TEXT NOT NULL,
    subject_user_id          BIGINT NOT NULL,
    token_digest             BYTEA NOT NULL UNIQUE,
    item_id                  TEXT,
    template_id              TEXT,
    binding_digest           CHAR(64),
    evidence_digest          CHAR(64),
    observation_fingerprint  CHAR(64),
    contract_digest          CHAR(64),
    target_digest            CHAR(64),
    decision_digest          CHAR(64),
    status                   TEXT NOT NULL DEFAULT 'active',
    operation_digest         CHAR(64),
    result_state             TEXT,
    result_version           INTEGER,
    issued_at                TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    expires_at               TIMESTAMPTZ NOT NULL,
    consumed_at              TIMESTAMPTZ,
    consumed_by              BIGINT,
    UNIQUE (workspace_id, operation_digest),
    FOREIGN KEY (tenant_id, workspace_id)
      REFERENCES workspaces(tenant_id, id) ON DELETE RESTRICT,
    FOREIGN KEY (tenant_id, workspace_id, intent_id)
      REFERENCES control_room_action_intents(tenant_id, workspace_id, id)
      ON DELETE RESTRICT,
    CONSTRAINT control_room_action_tokens_digest_chk
      CHECK (octet_length(token_digest) = 32),
    CONSTRAINT control_room_action_tokens_stage_chk
      CHECK (stage IN ('action_binding', 'workflow', 'approval', 'execution')),
    CONSTRAINT control_room_action_tokens_status_chk
      CHECK (status IN ('active', 'consumed', 'revoked')),
    CONSTRAINT control_room_action_tokens_shape_chk CHECK (
      (
        stage = 'action_binding' AND intent_id IS NULL
        AND item_id IS NOT NULL AND template_id = 'create_followup_task'
        AND binding_digest IS NOT NULL AND evidence_digest IS NOT NULL
        AND observation_fingerprint IS NOT NULL AND contract_digest IS NOT NULL
        AND target_digest IS NOT NULL AND decision_digest IS NOT NULL
      ) OR (
        stage <> 'action_binding' AND intent_id IS NOT NULL
        AND item_id IS NULL AND template_id IS NULL
        AND binding_digest IS NULL AND evidence_digest IS NULL
        AND observation_fingerprint IS NULL AND contract_digest IS NULL
        AND target_digest IS NULL AND decision_digest IS NULL
      )
    ),
    CONSTRAINT control_room_action_tokens_expiry_chk CHECK (
      expires_at > issued_at AND (
        (stage = 'action_binding' AND expires_at <= issued_at + INTERVAL '15 minutes')
        OR (stage = 'execution' AND expires_at <= issued_at + INTERVAL '5 minutes')
        OR (stage IN ('workflow', 'approval') AND expires_at <= issued_at + INTERVAL '24 hours')
      )
    ),
    CONSTRAINT control_room_action_tokens_operation_chk CHECK (
      operation_digest IS NULL OR operation_digest ~ '^[0-9a-f]{64}$'
    )
);

CREATE INDEX IF NOT EXISTS control_room_action_tokens_lookup_idx
    ON control_room_action_tokens(workspace_id, stage, status, expires_at);
CREATE INDEX IF NOT EXISTS control_room_action_tokens_intent_idx
    ON control_room_action_tokens(workspace_id, intent_id, stage);

CREATE TABLE IF NOT EXISTS control_room_action_intent_events (
    id                BIGSERIAL PRIMARY KEY,
    tenant_id         UUID NOT NULL,
    workspace_id      UUID NOT NULL,
    intent_id         UUID NOT NULL,
    actor_user_id     BIGINT NOT NULL,
    event_type        TEXT NOT NULL,
    from_state        TEXT NOT NULL,
    to_state          TEXT NOT NULL,
    intent_version    INTEGER NOT NULL,
    result_code       TEXT NOT NULL,
    correlation_id    UUID NOT NULL,
    operation_digest  CHAR(64) NOT NULL,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (workspace_id, intent_id, intent_version),
    UNIQUE (workspace_id, operation_digest),
    FOREIGN KEY (tenant_id, workspace_id, intent_id)
      REFERENCES control_room_action_intents(tenant_id, workspace_id, id)
      ON DELETE RESTRICT,
    CONSTRAINT control_room_action_intent_events_version_chk
      CHECK (intent_version >= 1),
    CONSTRAINT control_room_action_intent_events_type_chk
      CHECK (event_type IN (
        'intent_created', 'approval_claimed', 'approved', 'rejected',
        'stale', 'execution_reserved', 'completed', 'failed'
      )),
    CONSTRAINT control_room_action_intent_events_result_chk
      CHECK (result_code IN (
        'created', 'claimed', 'approved', 'rejected', 'stale',
        'execution_reserved', 'completed', 'failed'
      )),
    CONSTRAINT control_room_action_intent_events_digest_chk
      CHECK (operation_digest ~ '^[0-9a-f]{64}$')
);

CREATE INDEX IF NOT EXISTS control_room_action_intent_events_intent_idx
    ON control_room_action_intent_events(workspace_id, intent_id, created_at);

ALTER TABLE action_runs ADD COLUMN IF NOT EXISTS action_intent_id UUID;
CREATE INDEX IF NOT EXISTS action_runs_action_intent_idx
    ON action_runs(workspace_id, action_intent_id)
    WHERE action_intent_id IS NOT NULL;
ALTER TABLE action_runs
    DROP CONSTRAINT IF EXISTS action_runs_action_intent_fk;
ALTER TABLE action_runs
    ADD CONSTRAINT action_runs_action_intent_fk
    FOREIGN KEY (tenant_id, workspace_id, action_intent_id)
    REFERENCES control_room_action_intents(tenant_id, workspace_id, id)
    ON DELETE RESTRICT;

INSERT INTO schema_migrations (filename, applied_at)
VALUES ('99zzb_control_room_action_authority.sql', NOW())
ON CONFLICT (filename) DO NOTHING;
