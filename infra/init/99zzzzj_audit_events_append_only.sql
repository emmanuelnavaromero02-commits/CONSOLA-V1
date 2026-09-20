-- OMEGA — audit_events becomes append-only for the application role.
--
-- Until this migration, `omega_console` — the role the console runs as —
-- held SELECT, INSERT, UPDATE and DELETE on audit_events, because
-- 25_service_roles.sql grants those four verbs ON ALL TABLES and only ever
-- revokes vault_entries. A compromised or buggy console could therefore
-- edit or erase the record of what it had done, which is the one thing an
-- audit trail exists to prevent. The trail was, in effect, self-signed.
--
-- The application never needed those verbs: it only SELECTs (metrics,
-- dashboard, security routers) and INSERTs (audit_service, control_room
-- execution), and its INSERTs use ON CONFLICT DO NOTHING. The only DELETE
-- against this table anywhere in the tree is the one-off dedup in
-- 44_audit_events_dedup.sql, which runs as the `postgres` superuser during
-- migration and is unaffected by a grant revoked from omega_console.
--
-- Two layers, deliberately:
--   1. the grant, which is what actually stops the application;
--   2. a trigger, which also stops a session that has somehow acquired more
--      privilege than it should have.
--
-- The triggers are ENABLE ALWAYS on purpose. A plain trigger does not fire
-- when a session sets `session_replication_role = replica`, and a row-level
-- trigger never fires on TRUNCATE at all — the two gaps that make the
-- existing external_action_events guard weaker than it looks.
--
-- Break-glass, for whoever needs it later: legitimate maintenance (a
-- retention purge, a future dedup) is still possible as the table owner
-- with
--     ALTER TABLE audit_events DISABLE TRIGGER audit_events_no_update_delete;
-- performed deliberately, in its own transaction, and re-enabled after.
-- That is the point: erasing an audit record should require an explicit,
-- privileged, visible act rather than an ordinary application write.
--
-- Idempotent end to end: the REVOKE/GRANT pair is declarative, and each
-- trigger is dropped before being recreated.

-- ── Step 1: the grant, which is the fix ────────────────────────────────────

DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'omega_console') THEN
        REVOKE UPDATE, DELETE, TRUNCATE ON audit_events FROM omega_console;
        GRANT SELECT, INSERT ON audit_events TO omega_console;
    END IF;
END $$;

REVOKE UPDATE, DELETE, TRUNCATE ON audit_events FROM PUBLIC;

-- Future tables still inherit the four broad verbs from ALTER DEFAULT
-- PRIVILEGES in 25_service_roles.sql. That is a wider question than this
-- migration: narrowing it touches every table at once and is tracked
-- separately. This file closes the audit trail specifically.

-- ── Step 2: the trigger, as defence in depth ───────────────────────────────

CREATE OR REPLACE FUNCTION audit_events_append_only()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $$
BEGIN
    RAISE EXCEPTION
        'audit_events is append-only: % is not permitted on this table',
        TG_OP
        USING ERRCODE = 'insufficient_privilege',
              HINT = 'Disable the append-only trigger as the table owner if '
                     'this is deliberate, audited maintenance.';
END;
$$;

DROP TRIGGER IF EXISTS audit_events_no_update_delete ON audit_events;
CREATE TRIGGER audit_events_no_update_delete
    BEFORE UPDATE OR DELETE ON audit_events
    FOR EACH ROW
    EXECUTE FUNCTION audit_events_append_only();
ALTER TABLE audit_events
    ENABLE ALWAYS TRIGGER audit_events_no_update_delete;

DROP TRIGGER IF EXISTS audit_events_no_truncate ON audit_events;
CREATE TRIGGER audit_events_no_truncate
    BEFORE TRUNCATE ON audit_events
    FOR EACH STATEMENT
    EXECUTE FUNCTION audit_events_append_only();
ALTER TABLE audit_events
    ENABLE ALWAYS TRIGGER audit_events_no_truncate;
