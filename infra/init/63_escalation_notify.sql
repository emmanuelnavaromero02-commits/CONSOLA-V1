-- Mark which admins receive `request_admin_help` escalation emails.
-- Default false — if no user has it on, the tool falls back to the
-- ADMIN_EMAIL env var so existing installs keep working.

ALTER TABLE users
    ADD COLUMN IF NOT EXISTS escalation_notify BOOLEAN NOT NULL DEFAULT FALSE;

CREATE INDEX IF NOT EXISTS users_escalation_notify_idx
    ON users (escalation_notify)
    WHERE escalation_notify = TRUE;

DO $$
BEGIN
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'omega_mcp_infra') THEN
    GRANT SELECT (email, role, is_active, escalation_notify)
      ON users TO omega_mcp_infra;
  END IF;
END $$;
