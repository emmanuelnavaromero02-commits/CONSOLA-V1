-- F-SEG MAX-05: durable, actor-scoped idempotency for Workspace decision actions.
-- Raw caller keys are never stored; only SHA-256 digests reach this ledger.

CREATE TABLE IF NOT EXISTS workspace_decision_idempotency (
    id                      BIGSERIAL PRIMARY KEY,
    tenant_id               UUID NOT NULL,
    workspace_id            UUID NOT NULL,
    actor_user_id           BIGINT NOT NULL,
    operation               TEXT NOT NULL,
    idempotency_key_hash    CHAR(64) NOT NULL,
    request_fingerprint     CHAR(64) NOT NULL,
    status                  TEXT NOT NULL DEFAULT 'in_progress',
    action_id               BIGINT,
    created_at              TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    completed_at            TIMESTAMPTZ,
    CONSTRAINT workspace_decision_idempotency_scope_fk
        FOREIGN KEY (tenant_id, workspace_id)
        REFERENCES workspaces(tenant_id, id) ON DELETE CASCADE,
    CONSTRAINT workspace_decision_idempotency_actor_fk
        FOREIGN KEY (actor_user_id) REFERENCES users(id) ON DELETE CASCADE,
    CONSTRAINT workspace_decision_idempotency_action_fk
        FOREIGN KEY (action_id) REFERENCES decision_actions(id) ON DELETE SET NULL,
    CONSTRAINT workspace_decision_idempotency_operation_check
        CHECK (operation = btrim(operation) AND length(operation) BETWEEN 1 AND 160),
    CONSTRAINT workspace_decision_idempotency_key_hash_check
        CHECK (idempotency_key_hash ~ '^[0-9a-f]{64}$'),
    CONSTRAINT workspace_decision_idempotency_fingerprint_check
        CHECK (request_fingerprint ~ '^[0-9a-f]{64}$'),
    CONSTRAINT workspace_decision_idempotency_status_check
        CHECK (status IN ('in_progress', 'completed')),
    CONSTRAINT workspace_decision_idempotency_completion_check
        CHECK (
            (status = 'in_progress' AND action_id IS NULL AND completed_at IS NULL)
            OR
            (status = 'completed' AND completed_at IS NOT NULL)
        ),
    CONSTRAINT workspace_decision_idempotency_scope_key
        UNIQUE (actor_user_id, workspace_id, operation, idempotency_key_hash)
);

ALTER TABLE workspace_decision_idempotency ENABLE ROW LEVEL SECURITY;
ALTER TABLE workspace_decision_idempotency FORCE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS workspace_decision_idempotency_actor_scope
    ON workspace_decision_idempotency;
CREATE POLICY workspace_decision_idempotency_actor_scope
    ON workspace_decision_idempotency
    FOR ALL TO omega_workspace
    USING (
        omega_rls_workspace_matches(tenant_id, workspace_id)
        AND actor_user_id = NULLIF(current_setting('app.user_id', TRUE), '')::BIGINT
    )
    WITH CHECK (
        omega_rls_workspace_matches(tenant_id, workspace_id)
        AND actor_user_id = NULLIF(current_setting('app.user_id', TRUE), '')::BIGINT
    );

REVOKE ALL ON workspace_decision_idempotency FROM PUBLIC;
REVOKE DELETE, TRUNCATE ON workspace_decision_idempotency FROM omega_workspace;
GRANT SELECT, INSERT, UPDATE ON workspace_decision_idempotency TO omega_workspace;
-- Workspace already owns scoped decision-action writes, but the legacy grant
-- omitted SELECT; INSERT ... RETURNING and replay lookup therefore failed 42501.
GRANT SELECT, INSERT, UPDATE ON decision_actions TO omega_workspace;
REVOKE ALL ON SEQUENCE workspace_decision_idempotency_id_seq FROM PUBLIC;
GRANT USAGE, SELECT ON SEQUENCE workspace_decision_idempotency_id_seq
    TO omega_workspace;

INSERT INTO schema_migrations (filename, applied_at)
VALUES ('99zzz_workspace_decision_idempotency.sql', NOW())
ON CONFLICT (filename) DO NOTHING;
