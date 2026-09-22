-- OMEGA — harden the audit_events append-only guard (follow-up to 99zzzzj).
--
-- Three hardening steps on top of 99zzzzj:
--
--   1. audit_events.conversation_id no longer carries a foreign key. It was
--      ON DELETE SET NULL, so deleting a conversation rewrote audit rows; audit
--      evidence should not change because the thing it mentions was deleted.
--   2. The trigger function pins its search_path and reads the catalog through
--      fully qualified names, comparing the table owner by OID from TG_RELID
--      instead of looking it up by name. This is the standard hardening for any
--      function whose decision depends on catalog lookups.
--   3. The function's owner is pinned explicitly rather than inherited from
--      whoever happened to create it first.
--
-- 99zzzzj is recorded in schema_migrations by filename, so these changes live
-- in a new file: an edit to 99zzzzj would never reach an environment that has
-- already applied it.
--
-- A correction to 99zzzzj's own rationale: ALTER DEFAULT PRIVILEGES only
-- governs tables created later, so it never reaches audit_events. The way the
-- application role could regain these verbs is someone re-running a
-- GRANT ... ON ALL TABLES like the one in 25_service_roles.sql, which is the
-- case the trigger exists for.
--
-- Idempotent: the constraint loop finds nothing on a second run, the function
-- is replaced in place, and the closing check only reads the catalog.

-- ── 1. Referential actions must not rewrite audit rows ─────────────────────
-- The column keeps its value as a plain recorded fact; idx_audit_conversation
-- stays for lookups. This also removes a latent regression: with the trigger
-- in place, deleting any conversation that an audit row referenced failed for
-- every role except the owner.

DO $$
DECLARE
    fk record;
BEGIN
    FOR fk IN
        SELECT c.conname
          FROM pg_catalog.pg_constraint c
          JOIN pg_catalog.pg_attribute a
            ON a.attrelid = c.conrelid
           AND a.attnum = ANY (c.conkey)
         WHERE c.conrelid = 'public.audit_events'::pg_catalog.regclass
           AND c.contype = 'f'
           AND a.attname = 'conversation_id'
    LOOP
        EXECUTE pg_catalog.format(
            'ALTER TABLE public.audit_events DROP CONSTRAINT %I', fk.conname
        );
    END LOOP;
END $$;

-- ── 2. The trigger function: pinned search_path, owner compared by OID ─────
-- pg_temp is named explicitly and last, as PostgreSQL recommends for functions
-- that must not be influenced by the caller's session. Every catalog
-- reference is qualified, and the owner is read from the trigger's own
-- relation (TG_RELID), so renames and other schemas cannot affect it.
--
-- The owner stays exempt on purpose: a session that already owns the table
-- can drop the trigger or the table, so refusing it would deny nothing while
-- breaking retention purges, the dedup in 44_audit_events_dedup.sql and the
-- live test fixtures.

CREATE OR REPLACE FUNCTION public.audit_events_append_only()
RETURNS trigger
LANGUAGE plpgsql
SET search_path = pg_catalog, pg_temp
AS $$
DECLARE
    table_owner  oid;
    session_role oid;
BEGIN
    SELECT c.relowner
      INTO table_owner
      FROM pg_catalog.pg_class c
     WHERE c.oid = TG_RELID;

    SELECT r.oid
      INTO session_role
      FROM pg_catalog.pg_roles r
     WHERE r.rolname = session_user;

    IF table_owner IS NOT NULL AND session_role = table_owner THEN
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

-- ── 3. Ownership pinned to the superuser ───────────────────────────────────
ALTER FUNCTION public.audit_events_append_only() OWNER TO postgres;

-- ── 4. Refuse to commit unless every guarantee holds ───────────────────────
-- A security migration must not be able to half-apply silently. This block
-- only reads the catalog; if anything is off it raises and the whole
-- transaction rolls back.

DO $$
DECLARE
    fn oid := 'public.audit_events_append_only()'::pg_catalog.regprocedure;
BEGIN
    IF EXISTS (
        SELECT 1
          FROM pg_catalog.pg_constraint c
          JOIN pg_catalog.pg_attribute a
            ON a.attrelid = c.conrelid
           AND a.attnum = ANY (c.conkey)
         WHERE c.conrelid = 'public.audit_events'::pg_catalog.regclass
           AND c.contype = 'f'
           AND a.attname = 'conversation_id'
    ) THEN
        RAISE EXCEPTION 'audit_events.conversation_id still carries a foreign key';
    END IF;

    IF (SELECT p.proowner FROM pg_catalog.pg_proc p WHERE p.oid = fn)
       <> (SELECT r.oid FROM pg_catalog.pg_roles r WHERE r.rolname = 'postgres') THEN
        RAISE EXCEPTION 'audit_events_append_only() is not owned by postgres';
    END IF;

    IF NOT EXISTS (
        SELECT 1
          FROM pg_catalog.pg_proc p
         WHERE p.oid = fn
           AND 'search_path=pg_catalog, pg_temp' = ANY (p.proconfig)
    ) THEN
        RAISE EXCEPTION 'audit_events_append_only() does not pin search_path';
    END IF;

    IF (SELECT pg_catalog.count(*)
          FROM pg_catalog.pg_trigger t
         WHERE t.tgrelid = 'public.audit_events'::pg_catalog.regclass
           AND t.tgfoid = fn
           AND t.tgenabled = 'A'
           AND t.tgname IN ('audit_events_no_update_delete',
                            'audit_events_no_truncate')) <> 2 THEN
        RAISE EXCEPTION 'audit_events append-only triggers are missing or not ENABLE ALWAYS';
    END IF;
END $$;
