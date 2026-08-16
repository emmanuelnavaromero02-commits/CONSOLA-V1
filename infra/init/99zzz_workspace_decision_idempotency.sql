-- F-SEG MAX-05: durable, actor-scoped idempotency for Workspace and Console decision actions.
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
    FOR ALL TO omega_workspace, omega_console
    USING (
        omega_rls_workspace_matches(tenant_id, workspace_id)
        AND actor_user_id = NULLIF(current_setting('app.user_id', TRUE), '')::BIGINT
    )
    WITH CHECK (
        omega_rls_workspace_matches(tenant_id, workspace_id)
        AND actor_user_id = NULLIF(current_setting('app.user_id', TRUE), '')::BIGINT
    );

REVOKE ALL ON workspace_decision_idempotency FROM PUBLIC;
REVOKE DELETE, TRUNCATE ON workspace_decision_idempotency
    FROM omega_workspace, omega_console;
GRANT SELECT, INSERT, UPDATE ON workspace_decision_idempotency
    TO omega_workspace, omega_console;
-- Workspace already owns scoped decision-action writes, but the legacy grant
-- omitted SELECT; INSERT ... RETURNING and replay lookup therefore failed 42501.
GRANT SELECT, INSERT, UPDATE ON decision_actions TO omega_workspace;
-- DELETE is still authorization-gated by control_room.write in Workspace and
-- tenant/workspace-scoped by native RLS; the service role needs the matching
-- table privilege for an authorized administrator to complete the operation.
GRANT DELETE ON decisions TO omega_workspace;
-- The decisions BEFORE DELETE audit trigger must be able to append its
-- sanitized tombstone without granting the Workspace role a forgeable INSERT
-- capability on the forensic ledger itself.
ALTER FUNCTION soft_delete_audit_trigger() SECURITY DEFINER;
ALTER FUNCTION soft_delete_audit_trigger()
    SET search_path = pg_catalog, public, pg_temp;

-- Cross-scope references are a database invariant, not an API convention.
-- Existing rows are intentionally not scanned or rewritten. The trigger runs
-- for new rows and only when a reference/scope column is changed, so unrelated
-- updates to historical data keep working.
CREATE OR REPLACE FUNCTION enforce_decision_reference_scope()
RETURNS TRIGGER
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public, pg_temp
AS $decision_scope$
DECLARE
    scoped_tenant UUID;
BEGIN
    IF TG_OP = 'UPDATE'
       AND NEW.workspace_id IS NOT DISTINCT FROM OLD.workspace_id
       AND NEW.assignee_id IS NOT DISTINCT FROM OLD.assignee_id
       AND NEW.follow_up_decision_id IS NOT DISTINCT FROM OLD.follow_up_decision_id THEN
        RETURN NEW;
    END IF;

    IF NEW.assignee_id IS NULL AND NEW.follow_up_decision_id IS NULL THEN
        RETURN NEW;
    END IF;
    IF NEW.workspace_id IS NULL THEN
        RAISE EXCEPTION 'decision references require a workspace'
            USING ERRCODE = '23514',
                  CONSTRAINT = 'decisions_reference_scope_check';
    END IF;

    SELECT w.tenant_id
      INTO scoped_tenant
      FROM public.workspaces AS w
     WHERE w.id = NEW.workspace_id
     FOR SHARE;
    IF scoped_tenant IS NULL THEN
        RAISE EXCEPTION 'decision workspace is invalid'
            USING ERRCODE = '23514',
                  CONSTRAINT = 'decisions_reference_scope_check';
    END IF;

    IF NEW.assignee_id IS NOT NULL THEN
        PERFORM 1
          FROM public.users AS u
          JOIN public.user_workspace_roles AS uwr ON uwr.user_id = u.id
         WHERE u.id = NEW.assignee_id
           AND u.tenant_id = scoped_tenant
           AND u.is_active = TRUE
           AND uwr.workspace_id = NEW.workspace_id
         LIMIT 1
         FOR SHARE OF u, uwr;
        IF NOT FOUND THEN
            RAISE EXCEPTION 'decision assignee is outside the active scope'
                USING ERRCODE = '23514',
                      CONSTRAINT = 'decisions_assignee_scope_check';
        END IF;
    END IF;

    IF NEW.follow_up_decision_id IS NOT NULL THEN
        PERFORM 1
          FROM public.decisions AS followed
         WHERE followed.id = NEW.follow_up_decision_id
           AND followed.workspace_id = NEW.workspace_id
         FOR SHARE OF followed;
        IF NOT FOUND THEN
            RAISE EXCEPTION 'follow-up decision is outside the active scope'
                USING ERRCODE = '23514',
                      CONSTRAINT = 'decisions_follow_up_scope_check';
        END IF;
    END IF;
    RETURN NEW;
END;
$decision_scope$;

REVOKE ALL ON FUNCTION enforce_decision_reference_scope() FROM PUBLIC;
DROP TRIGGER IF EXISTS trg_decisions_reference_scope ON decisions;
CREATE TRIGGER trg_decisions_reference_scope
BEFORE INSERT OR UPDATE ON decisions
FOR EACH ROW EXECUTE FUNCTION enforce_decision_reference_scope();

REVOKE ALL ON SEQUENCE workspace_decision_idempotency_id_seq FROM PUBLIC;
GRANT USAGE, SELECT ON SEQUENCE workspace_decision_idempotency_id_seq
    TO omega_workspace, omega_console;

INSERT INTO schema_migrations (filename, applied_at)
VALUES ('99zzz_workspace_decision_idempotency.sql', NOW())
ON CONFLICT (filename) DO NOTHING;
