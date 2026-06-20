-- Gold refresh intelligence automation.
--
-- Allows the Intelligence run history to distinguish runs triggered
-- automatically after Gold materialization from manual/scheduled runs.

DO $$
BEGIN
    IF to_regclass('public.intelligence_runs') IS NOT NULL THEN
        ALTER TABLE public.intelligence_runs
            DROP CONSTRAINT IF EXISTS intelligence_runs_mode_chk;
        ALTER TABLE public.intelligence_runs
            ADD CONSTRAINT intelligence_runs_mode_chk
            CHECK (run_mode IN ('manual', 'scheduled', 'backtest', 'smoke', 'gold_refresh'));
    END IF;
END $$;

INSERT INTO schema_migrations (filename, applied_at)
VALUES ('99y_gold_refresh_intelligence.sql', NOW())
ON CONFLICT (filename) DO NOTHING;
