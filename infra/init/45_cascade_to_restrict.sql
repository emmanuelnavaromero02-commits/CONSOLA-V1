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
        'user_workspace_roles',
        'conversations',
        'conversation_messages',
        'workspaces',
        'decisions',
        'refresh_tokens',
        'user_sessions'
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

CREATE OR REPLACE FUNCTION soft_delete_audit_trigger()
RETURNS TRIGGER AS $$
BEGIN
  INSERT INTO audit_deletes (table_name, deleted_pk, deleted_row, deleted_at)
  VALUES (TG_TABLE_NAME, OLD.id::TEXT, to_jsonb(OLD), NOW());
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
