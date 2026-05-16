-- Sprint v1.44.1 (R1 DB review follow-up) — indexes the dashboard
-- KPI poll needs to stay cheap.
--
-- console/app/routers/dashboard.py runs 6 short SELECTs every 30s
-- per connected user. The v1.40 / v1.42 indexes cover most of them,
-- but two queries still seq-scan on the post-v1.44.1 layout:
--
--   1. _freshness_per_cartridge:  MAX(finished_at) GROUP BY cartridge_id
--      WHERE status = 'success'
--      → existing idx_extraction_runs_started_at doesn't help; we
--        need a (cartridge_id, finished_at) partial covering the
--        success branch.
--
--   2. _copilot_counts:  COUNT(*) FROM conversations WHERE created_at >= today
--      → conversations table only had idx_conversations_user_workspace
--        (user_id, workspace_id, updated_at). A standalone created_at
--        index keeps the date-trunc COUNT scan-free.
--
-- Both ``CREATE INDEX IF NOT EXISTS`` so the migration is idempotent
-- and safe on re-apply.

-- 1) extraction_runs (cartridge_id, finished_at) WHERE status = 'success'
--    Partial index: only successful rows ever contribute to "last
--    successful extraction" → keeps the index small (typically <5%
--    the size of extraction_runs).
CREATE INDEX IF NOT EXISTS idx_extraction_runs_cartridge_finished_success
    ON extraction_runs (cartridge_id, finished_at DESC)
 WHERE status = 'success';

-- 2) conversations.created_at — standalone, supports the daily
--    rollup. The v1.42 composite (user_id, workspace_id,
--    updated_at) doesn't help a date-trunc COUNT that ignores
--    user/workspace.
CREATE INDEX IF NOT EXISTS idx_conversations_created_at
    ON conversations (created_at);

-- Register migration
INSERT INTO schema_migrations (filename, applied_at)
VALUES ('49_dashboard_kpi_indexes.sql', NOW())
ON CONFLICT (filename) DO NOTHING;
