-- v1.44.5 — Persist X-Request-ID on audit events for request correlation.

ALTER TABLE IF EXISTS audit_events
    ADD COLUMN IF NOT EXISTS request_id TEXT;

CREATE INDEX IF NOT EXISTS idx_audit_events_request_id
    ON audit_events (request_id);
