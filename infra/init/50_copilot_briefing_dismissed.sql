-- Sprint v1.44.2 (Tarea F) — per-user dismissal of proactive
-- briefing highlights.
--
-- The proactive_service.briefing_for_user() pipeline produces
-- highlights with deterministic ids like "freshness:sap_hcm". When
-- a user clicks "Ignorar" on a highlight we record (user_id,
-- highlight_id) here so the same highlight stops surfacing for that
-- user. The dismissal is permanent — the user can re-enable a
-- category by clearing all dismissals (future UI affordance) or by
-- DELETE'ing the row through a forthcoming v1.44.2 endpoint.
--
-- Idempotent end-to-end: CREATE TABLE IF NOT EXISTS, CREATE INDEX
-- IF NOT EXISTS, INSERT … ON CONFLICT DO NOTHING.

CREATE TABLE IF NOT EXISTS copilot_briefing_dismissed (
    user_id        BIGINT      NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    highlight_id   TEXT        NOT NULL,
    dismissed_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (user_id, highlight_id)
);

-- The briefing fetch path scans by user_id and joins on highlight_id.
-- The PK already covers the common ``WHERE user_id = $1`` lookup; an
-- ad-hoc analytics query that wants "how many users dismissed X"
-- needs the reverse index.
CREATE INDEX IF NOT EXISTS idx_copilot_briefing_dismissed_highlight
    ON copilot_briefing_dismissed (highlight_id);

-- Self-register in schema_migrations (the v1.43+ pattern —
-- docker-entrypoint-initdb.d never invokes apply_db_migrations.sh).
INSERT INTO schema_migrations (filename, applied_at)
VALUES ('50_copilot_briefing_dismissed.sql', NOW())
ON CONFLICT (filename) DO NOTHING;
