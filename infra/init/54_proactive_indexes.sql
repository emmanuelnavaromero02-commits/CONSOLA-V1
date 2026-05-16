-- Sprint v1.44.2 (R1 DB review follow-up) — indexes the briefing
-- queries need to stay cheap.
--
-- The Round 1 DB review caught that two of the proactive analyzers
-- ran on tables without supporting indexes:
--
--   * analyze_extraction_failures (P1) — filters
--       status='failed' AND finished_at >= NOW() - INTERVAL '24 hours'
--     migration 49 only indexed the success branch
--     (idx_extraction_runs_cartridge_finished_success) so the failed
--     query seq-scans extraction_runs every briefing poll.
--
--   * analyze_volume_anomaly (P2) — scans the 8-day window with
--     status='success'; migration 49's partial leads with
--     cartridge_id so the date-range scan re-filters. A partial
--     index leading on finished_at gives a clean range probe.
--
-- Both are CREATE INDEX IF NOT EXISTS so the migration is idempotent
-- and safe on re-apply.

-- 1) Partial index on failed extractions (status='failed') ordered by
--    finished_at DESC. Failed rows are typically <5% of extraction_runs,
--    so the partial keeps the index small while covering the briefing's
--    24-hour scan.
CREATE INDEX IF NOT EXISTS idx_extraction_runs_failed_finished
    ON extraction_runs (finished_at DESC)
 WHERE status = 'failed';

-- 2) Partial index on successful extractions ordered by finished_at
--    (without the cartridge_id lead). analyze_volume_anomaly does an
--    8-day range scan grouped by cartridge_id; PG can range-scan this
--    and then GROUP BY without revisiting the cartridge partition.
--    The migration 49 index stays useful for the freshness MAX()
--    GROUP BY (different access pattern).
CREATE INDEX IF NOT EXISTS idx_extraction_runs_success_finished
    ON extraction_runs (finished_at DESC)
 WHERE status = 'success';

-- Register migration.
INSERT INTO schema_migrations (filename, applied_at)
VALUES ('54_proactive_indexes.sql', NOW())
ON CONFLICT (filename) DO NOTHING;
