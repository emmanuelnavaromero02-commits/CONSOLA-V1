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
-- The guard uses ``WHERE onboarding_completed IS FALSE`` so a
-- re-run after the column already exists is a no-op (the second
-- run finds nothing to update).
UPDATE users
   SET onboarding_completed = TRUE
 WHERE onboarding_completed = FALSE
   AND created_at < NOW();
-- NOTE: this is the one-shot backfill. Strictly speaking, the
-- ``created_at < NOW()`` predicate is redundant for the first
-- application of this migration (every existing row satisfies it),
-- but it keeps the statement deterministic across replays AND
-- documents the intent: only rows that existed BEFORE this
-- migration's clock-reading get auto-completed. Anything created
-- after this migration ran is treated as a new user and walks the
-- wizard.

-- Self-register in schema_migrations (matches the 43-47 pattern —
-- docker-entrypoint-initdb.d never invokes the runner script).
INSERT INTO schema_migrations (filename, applied_at)
VALUES ('48_users_onboarding_completed.sql', NOW())
ON CONFLICT (filename) DO NOTHING;
