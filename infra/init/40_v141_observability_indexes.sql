-- Sprint v1.41.1 — Indexes for the new observability endpoints.
--
-- /api/metrics/operational issues three counters and a top-5 aggregate
-- against extraction_runs filtering only by started_at (no cartridge_id
-- predicate, so the existing composite idx_extraction_runs_cartridge
-- can't be used in seek mode), plus one counter against audit_events
-- filtering only by created_at. Without these indexes those queries
-- seq-scan the full tables once volume grows.
--
-- Idempotent: every CREATE INDEX uses IF NOT EXISTS so a re-applied
-- migration (or a docker compose up against an already-migrated
-- volume) is a no-op.

-- audit_events: counter for last 24h activity volume.
CREATE INDEX IF NOT EXISTS idx_audit_events_created_at
    ON audit_events (created_at DESC);

-- extraction_runs: counters by started_at window (24h / 7d) and the
-- success-only AVG / slowest-5 aggregates.
CREATE INDEX IF NOT EXISTS idx_extraction_runs_started_at
    ON extraction_runs (started_at DESC);

-- extraction_runs LATERAL pull in /api/freshness joins by
-- (cartridge_id, entity_name) ordered by started_at DESC. The composite
-- below turns that LATERAL into a single 3-column index seek.
CREATE INDEX IF NOT EXISTS idx_extraction_runs_cartridge_entity_started
    ON extraction_runs (cartridge_id, entity_name, started_at DESC);
