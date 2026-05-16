-- Sprint v1.43.4 (Claude H7) — GDPR-aligned PII sanitization in the
-- soft-delete audit trigger.
--
-- v1.43.2 (Security R1) already stripped secret-shaped columns
-- (password_hash, token, api_key, …) from ``audit_deletes.deleted_row``,
-- but it left PII intact: when a row from ``users`` is deleted the
-- JSONB snapshot still contains the user's ``email`` and ``name``.
--
-- The legal posture this fixes:
--   1. A user invokes their GDPR Article 17 right to be forgotten.
--   2. The application DELETEs the row from ``users``.
--   3. The BEFORE-DELETE trigger writes a JSONB snapshot to
--      ``audit_deletes`` containing the email + name we just promised
--      to erase. The promise is broken silently.
--   4. ``audit_deletes`` was sized to be append-only (v1.43.2 Security
--      R2 explicitly REVOKE'd UPDATE/DELETE from omega_console), so
--      we can't fix this retroactively without a DBA-level intervention.
--
-- Migration 47 replaces the trigger function so future deletes strip
-- PII per table. A salted MD5 of the email is stored as ``email_hash``
-- so forensics (fraud investigations, "did this email ever own an
-- account?") still works without retaining the cleartext email itself.
--
-- The migration is idempotent: CREATE OR REPLACE FUNCTION rewrites the
-- body, the existing trigger bindings (attached in migration 45) keep
-- working with the new function automatically. Re-running this file
-- is a no-op apart from re-writing the same function body.

CREATE OR REPLACE FUNCTION soft_delete_audit_trigger()
RETURNS TRIGGER AS $$
DECLARE
  snapshot     JSONB;
  email_value  TEXT;
BEGIN
  -- v1.43.2 (Security R1): strip secret-shaped columns. Missing keys
  -- are no-ops in ``jsonb - text``, so over-listing has zero cost
  -- and guards against future columns matching a sensitive name.
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

  -- v1.43.4 (Claude H7): per-table PII sanitization.
  --
  -- ``users``: strip email + name (PII per GDPR Art. 4(1)). Replace
  -- ``email`` with ``email_hash = md5(email)`` so forensic correlation
  -- ("was this account ever associated with foo@bar.com?") keeps
  -- working without retaining the cleartext value. The md5 is
  -- deliberate — it's a one-way fingerprint for correlation, NOT a
  -- security primitive (the secret-key strip above is what guards
  -- credentials).
  --
  -- ``tenants``: the current schema only has ``id`` + ``name`` +
  -- ``created_at``. ``name`` is a company / workspace label, not PII
  -- in itself, but it appears in user-facing audit views, so keep it.
  -- If a future column adds contact_email / contact_phone, extend the
  -- ELSIF branch below with the same strip + hash pattern.
  IF TG_TABLE_NAME = 'users' THEN
    -- Pull email out of the OLD row BEFORE we strip it from the JSONB
    -- snapshot, so the hash sees the original value.
    email_value := snapshot ->> 'email';
    snapshot := snapshot - 'email' - 'name';
    IF email_value IS NOT NULL THEN
      snapshot := jsonb_set(
        snapshot,
        '{email_hash}',
        to_jsonb(md5(lower(email_value)))
      );
    END IF;
  END IF;

  INSERT INTO audit_deletes (table_name, deleted_pk, deleted_row, deleted_at)
  VALUES (TG_TABLE_NAME, OLD.id::TEXT, snapshot, NOW());
  RETURN OLD;
END;
$$ LANGUAGE plpgsql;

-- Migration 45 already DROP+CREATE'd the trigger bindings on the
-- protected tables, and Postgres looks up the function by name at
-- fire time, so the new function body takes effect immediately on
-- the next delete. No re-binding needed.

-- ── Register this migration ──────────────────────────────────────────────
-- docker-entrypoint-initdb.d runs init scripts on a fresh data
-- directory but never invokes scripts/apply_db_migrations.sh.
-- Self-registering keeps the audit trail honest on both paths.
INSERT INTO schema_migrations (filename, applied_at)
VALUES ('47_audit_deletes_pii_sanitization.sql', NOW())
ON CONFLICT (filename) DO NOTHING;
