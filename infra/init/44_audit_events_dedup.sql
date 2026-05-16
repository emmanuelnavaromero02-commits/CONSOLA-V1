-- Sprint v1.43.2 (Claude B5) — Deduplicate audit_events + UNIQUE
-- constraint to prevent forensia inflation on retries.
--
-- Pre-v1.43.2, every retry of an admin action stamped a fresh row in
-- audit_events even when the (user_id, action, resource_id, created_at)
-- tuple was already present. A flapping operation could double / triple
-- its own audit trail, making "how many times did X happen?" answer
-- meaningless during forensic review.
--
-- Step 1: collapse pre-existing exact duplicates, keeping the earliest
-- id per group so any FK that references that id (today: none) stays
-- valid. Confined to rows with non-NULL user_id + action so the cleanup
-- never touches login_attempts / system rows.
WITH dedup AS (
  SELECT id,
         ROW_NUMBER() OVER (
           PARTITION BY user_id, action, resource_id, created_at
           ORDER BY id ASC
         ) AS rn
  FROM audit_events
  WHERE user_id IS NOT NULL
    AND action  IS NOT NULL
)
DELETE FROM audit_events
WHERE id IN (SELECT id FROM dedup WHERE rn > 1);

-- Step 2: enforce uniqueness going forward.
--
-- Postgres 15+ supports ``UNIQUE NULLS NOT DISTINCT`` so duplicate NULLs
-- are also rejected. The local + AWS stacks both run pgvector/pgvector:pg15
-- (see infra/docker-compose.yml), so the path is safe — but we keep a
-- partial-unique-index fallback for any older standby a customer may
-- still run.
DO $$
BEGIN
  IF EXISTS (
    SELECT 1 FROM pg_constraint
    WHERE conname = 'audit_events_dedup_uniq'
  ) THEN
    RAISE NOTICE 'audit_events_dedup_uniq already exists, skipping';
  ELSIF EXISTS (
    SELECT 1 FROM pg_indexes
    WHERE schemaname = 'public'
      AND indexname = 'audit_events_dedup_uniq_idx'
  ) THEN
    RAISE NOTICE 'audit_events_dedup_uniq_idx already exists, skipping';
  ELSE
    BEGIN
      EXECUTE 'ALTER TABLE audit_events
                 ADD CONSTRAINT audit_events_dedup_uniq
                 UNIQUE NULLS NOT DISTINCT
                 (user_id, action, resource_id, created_at)';
    EXCEPTION WHEN syntax_error OR feature_not_supported THEN
      -- Older Postgres: emulate via partial unique index restricted to
      -- rows where every key column is non-NULL.
      EXECUTE 'CREATE UNIQUE INDEX IF NOT EXISTS audit_events_dedup_uniq_idx
                 ON audit_events (user_id, action, resource_id, created_at)
                 WHERE user_id IS NOT NULL';
    END;
  END IF;
END $$;


-- ── Register in schema_migrations ─────────────────────────────────────────
-- v1.43.2 (DevOps R1 hardening): see 45_cascade_to_restrict.sql for the
-- rationale — fresh installs run init scripts via docker-entrypoint
-- and never go through apply_db_migrations.sh, so the migration must
-- self-stamp here.
INSERT INTO schema_migrations (filename, applied_at)
VALUES ('44_audit_events_dedup.sql', NOW())
ON CONFLICT (filename) DO NOTHING;
