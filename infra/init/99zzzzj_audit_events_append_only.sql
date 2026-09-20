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
-- existing external_action_events guard weaker than it looks. ENABLE ALWAYS
-- plus a statement-level TRUNCATE trigger closes both, for every role that
-- is not the table owner.
--
-- The owner is exempt, and the exemption is honest rather than reluctant: a
-- session that is already the owner can DROP the trigger or the table, so
-- blocking it would deny nothing while breaking legitimate maintenance — a
-- retention purge, the historical dedup in 44_audit_events_dedup.sql, and
-- the live test fixtures that clear audit rows between cases.
--
-- So the guarantee this migration actually makes, stated plainly: no role
-- other than the owner can alter or remove an audit row, by any route,
-- including session_replication_role = replica and TRUNCATE.
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
DECLARE
    owner_name TEXT;
BEGIN
    SELECT pg_get_userbyid(c.relowner)
      INTO owner_name
      FROM pg_class c
      JOIN pg_namespace n ON n.oid = c.relnamespace
     WHERE c.relname = 'audit_events' AND n.nspname = 'public';

    -- The owner is exempt, and deliberately so. Against a session that is
    -- already the owner this trigger is theatre: it could DROP the trigger,
    -- ALTER the table, or drop it outright. Pretending otherwise would only
    -- cost legitimate maintenance — retention purges, the historical dedup in
    -- 44_audit_events_dedup.sql, and test fixtures that truncate between
    -- cases — without denying a capability anyone actually lacks.
    --
    -- What the trigger does buy is the case the grant alone does not cover:
    -- ALTER DEFAULT PRIVILEGES in 25_service_roles.sql keeps handing UPDATE
    -- and DELETE on new tables to omega_console, so a future role can acquire
    -- those verbs by accident. Such a role is not the owner, so it is stopped
    -- here even if someone forgets the REVOKE.
    IF owner_name IS NOT NULL AND session_user = owner_name THEN
        IF TG_LEVEL = 'STATEMENT' THEN
            RETURN NULL;          -- BEFORE TRUNCATE: NEW/OLD are not assigned
        END IF;
        IF TG_OP = 'DELETE' THEN
            RETURN OLD;
        END IF;
        RETURN NEW;
    END IF;

    RAISE EXCEPTION
        'audit_events is append-only: % is not permitted on this table',
        TG_OP
        USING ERRCODE = 'insufficient_privilege',
              HINT = 'Only the table owner may remove audit rows, and only as '
                     'deliberate, audited maintenance.';
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
