-- Sprint v1.43.2 (Claude B6) — Convert ON DELETE CASCADE → RESTRICT
-- on critical edges to block accidental mass-deletion. A soft-delete
-- BEFORE-DELETE trigger snapshots the row into ``audit_deletes`` so
-- legitimate deletes still leave a forensic trail.
--
-- The pre-v1.43.2 chain was:
--    tenants → workspaces → user_workspace_roles → conversations / messages
-- All FKs along that path used ON DELETE CASCADE. A single
-- ``DELETE FROM tenants WHERE id = …`` could erase an entire tenant's
-- workspaces, role assignments and conversations with zero trace
-- (CASCADE silently propagates without firing per-row audit logic).
-- Switching to RESTRICT forces the operator (or the application) to
-- delete dependants explicitly, which is what we want for tenant /
-- workspace / user / conversation lifecycle operations.
--
-- Idempotent end-to-end: every CREATE TABLE uses IF NOT EXISTS, every
-- CREATE INDEX uses IF NOT EXISTS, the FK swap is wrapped in a DO
-- block that re-reads the catalog to find current CASCADE constraints,
-- and the trigger CREATE re-DROPs first.

-- ── Step 1: tombstone table for soft-deletes ───────────────────────────────

CREATE TABLE IF NOT EXISTS audit_deletes (
    id              BIGSERIAL PRIMARY KEY,
    table_name      TEXT NOT NULL,
    deleted_pk      TEXT NOT NULL,
    deleted_row     JSONB NOT NULL,
    deleted_by      BIGINT,                    -- user_id when available
    deleted_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    reason          TEXT
);
CREATE INDEX IF NOT EXISTS idx_audit_deletes_table ON audit_deletes (table_name);
CREATE INDEX IF NOT EXISTS idx_audit_deletes_at    ON audit_deletes (deleted_at DESC);

-- v1.43.2 (Security R1 hardening): the deleted_row JSONB can contain
-- PII (users.email, users.full_name) and — even after the secret-key
-- sanitization below — must not be world-readable. Mirror the
-- vault_entries posture from infra/init/25_service_roles.sql:
-- least-privilege; only postgres / admin roles can SELECT.
REVOKE ALL ON audit_deletes FROM PUBLIC;
DO $$
BEGIN
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'omega_console') THEN
    -- console owns the user-deletion flow and needs to write tombstones.
    EXECUTE 'GRANT INSERT, SELECT ON audit_deletes TO omega_console';
    EXECUTE 'GRANT USAGE, SELECT ON SEQUENCE audit_deletes_id_seq TO omega_console';
  END IF;
END $$;


-- ── Step 2: convert CASCADE → RESTRICT on critical FKs ────────────────────
--
-- Walk the catalog rather than hard-coding constraint names so the
-- migration tolerates name drift across environments / older Postgres
-- versions that auto-named the FK differently.

DO $$
DECLARE
  fk RECORD;
BEGIN
  FOR fk IN
    SELECT tc.constraint_name,
           tc.table_name,
           kcu.column_name,
           ccu.table_name AS ref_table
    FROM information_schema.table_constraints   tc
    JOIN information_schema.key_column_usage    kcu
      ON tc.constraint_name = kcu.constraint_name
    JOIN information_schema.constraint_column_usage ccu
      ON ccu.constraint_name = tc.constraint_name
    JOIN information_schema.referential_constraints rc
      ON rc.constraint_name = tc.constraint_name
    WHERE tc.constraint_type = 'FOREIGN KEY'
      AND rc.delete_rule     = 'CASCADE'
      AND tc.table_name      IN (
        -- v1.43.2 (DB R1 hardening): refresh_tokens / user_sessions were
        -- intentionally REMOVED. They are short-lived auth-lifecycle
        -- records, not the tenant→workspace→conversation forensic
        -- chain this migration protects. Flipping them to RESTRICT
        -- would break console/app/services/auth.py::delete_user() —
        -- which does a bare ``DELETE FROM users WHERE id = $1`` —
        -- because every user with an active session would now fail
        -- the FK check. CASCADE here is the correct semantic.
        'user_workspace_roles',
        'conversations',
        'conversation_messages',
        'workspaces',
        'decisions'
      )
      AND ccu.table_name     IN (
        'tenants', 'users', 'workspaces', 'roles', 'conversations'
      )
  LOOP
    EXECUTE format(
      'ALTER TABLE %I DROP CONSTRAINT %I',
      fk.table_name, fk.constraint_name
    );
    EXECUTE format(
      'ALTER TABLE %I ADD CONSTRAINT %I FOREIGN KEY (%I) '
      'REFERENCES %I ON DELETE RESTRICT',
      fk.table_name, fk.constraint_name, fk.column_name, fk.ref_table
    );
    RAISE NOTICE 'Converted % on %.% → RESTRICT',
      fk.constraint_name, fk.table_name, fk.column_name;
  END LOOP;
END $$;


-- ── Step 3: BEFORE-DELETE trigger that snapshots the row ──────────────────
--
-- Captures the entire row as JSONB. The trigger fires per row and
-- returns OLD so the actual delete proceeds afterwards. Keep the
-- function generic so attaching it to additional tables is a
-- one-line CREATE TRIGGER.
--
-- ``deleted_by`` is left NULL here — we don't have access to the
-- application user_id from inside a trigger; the application layer is
-- responsible for stamping that via a separate audit_events row when
-- it intentionally deletes.

-- v1.43.2 (Security R1 hardening): the BEFORE-DELETE trigger fires on
-- ``users`` (among others), and ``users.password_hash`` is the bcrypt
-- digest of the user's password. ``to_jsonb(OLD)`` would persist that
-- hash into ``audit_deletes.deleted_row`` forever — and bcrypt is
-- offline-crackable. Strip every known secret-shaped column from the
-- JSONB before insert. The list is deliberately broad: missing keys
-- are no-ops in ``jsonb - text``, so over-listing has zero cost and
-- guards against future columns that match a sensitive name.
CREATE OR REPLACE FUNCTION soft_delete_audit_trigger()
RETURNS TRIGGER AS $$
DECLARE
  snapshot JSONB;
BEGIN
  snapshot := to_jsonb(OLD)
              - 'password_hash'
              - 'password'
              - 'token'
              - 'access_token'
              - 'refresh_token'
              - 'api_key'
              - 'secret'
              - 'private_key'
              - 'session_token'
              - 'csrf_secret'
              - 'mfa_secret'
              - 'totp_secret';
  INSERT INTO audit_deletes (table_name, deleted_pk, deleted_row, deleted_at)
  VALUES (TG_TABLE_NAME, OLD.id::TEXT, snapshot, NOW());
  RETURN OLD;
END;
$$ LANGUAGE plpgsql;


-- ── Step 4: attach the trigger to the protected tables ────────────────────
--
-- DROP TRIGGER IF EXISTS first so the migration is rerunnable. Each
-- attach is guarded with a table-existence check so the migration
-- doesn't blow up on a partial install where one of the dependants
-- was never created.

DO $$
DECLARE
  tbl TEXT;
BEGIN
  FOREACH tbl IN ARRAY ARRAY['tenants', 'workspaces', 'users',
                              'conversations', 'decisions']
  LOOP
    IF EXISTS (
      SELECT 1 FROM information_schema.tables
      WHERE table_schema = 'public' AND table_name = tbl
    ) THEN
      EXECUTE format(
        'DROP TRIGGER IF EXISTS trg_soft_delete_%I ON %I',
        tbl, tbl
      );
      EXECUTE format(
        'CREATE TRIGGER trg_soft_delete_%I '
        'BEFORE DELETE ON %I '
        'FOR EACH ROW EXECUTE FUNCTION soft_delete_audit_trigger()',
        tbl, tbl
      );
      RAISE NOTICE 'Attached soft-delete trigger to %', tbl;
    ELSE
      RAISE NOTICE 'Skipping soft-delete trigger on missing table %', tbl;
    END IF;
  END LOOP;
END $$;


-- ── Step 5: register in schema_migrations ─────────────────────────────────
-- v1.43.2 (DevOps R1 hardening): docker-entrypoint-initdb.d runs init
-- scripts on a fresh data directory but never goes through
-- scripts/apply_db_migrations.sh (which is what normally stamps the
-- row). Migration 43 backfills 00-42; 44 and 45 must self-register or
-- a fresh install will show them physically applied but untracked.
INSERT INTO schema_migrations (filename, applied_at)
VALUES ('45_cascade_to_restrict.sql', NOW())
ON CONFLICT (filename) DO NOTHING;
