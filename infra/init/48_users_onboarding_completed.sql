-- Sprint v1.44.1 (Tarea F) — onboarding completion flag on users.
--
-- The first-time user wizard (5 steps) needs a per-user "did you
-- complete the tour?" bit. Storing it on the users row keeps the
-- query path trivial (the wizard's autostart check piggybacks on
-- the existing /api/me round-trip) and avoids creating a separate
-- one-row-per-user table for what's effectively a boolean.
--
-- Idempotent: ALTER TABLE ... ADD COLUMN IF NOT EXISTS is the
-- canonical Postgres pattern for additive schema changes; this
-- migration is safe on a partially-bootstrapped DB and on a re-run.

ALTER TABLE users
    ADD COLUMN IF NOT EXISTS onboarding_completed BOOLEAN
    NOT NULL DEFAULT FALSE;

-- Existing users (pre-v1.44.1) should NOT be force-walked through
-- the wizard — they already know the product. Mark every existing
-- row as completed so only genuinely new accounts trigger the tour.
--
-- Replay-safety gate: this UPDATE only runs the FIRST time the
-- migration is applied. The Round 1 DB review caught that the
-- prior ``WHERE created_at < NOW()`` predicate was NOT replay-safe
-- in the sense its comment claimed: a user who registered between
-- two migration applications would silently get their wizard
-- skipped on the second run (they're FALSE by default + their
-- created_at < second-run NOW). Gating on the schema_migrations
-- row makes the backfill genuinely one-shot — every subsequent
-- run skips the UPDATE entirely.
DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM schema_migrations
     WHERE filename = '48_users_onboarding_completed.sql'
  ) THEN
    UPDATE users
       SET onboarding_completed = TRUE
     WHERE onboarding_completed = FALSE;
    RAISE NOTICE 'migration 48: backfilled existing users to onboarding_completed=TRUE';
  ELSE
    RAISE NOTICE 'migration 48: backfill skipped (already applied)';
  END IF;
END $$;

-- Self-register in schema_migrations (matches the 43-47 pattern —
-- docker-entrypoint-initdb.d never invokes the runner script).
-- Order matters: the backfill above READS this table, so the
-- INSERT MUST come AFTER the UPDATE block, not before.
INSERT INTO schema_migrations (filename, applied_at)
VALUES ('48_users_onboarding_completed.sql', NOW())
ON CONFLICT (filename) DO NOTHING;
