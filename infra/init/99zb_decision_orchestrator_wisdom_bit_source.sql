-- Allow AgentOps WisdomBit monitors to create advisory decision orchestrations.
-- Existing deployments already have decision_orchestration_runs, so this
-- migration reconciles the source_type CHECK constraint without rebuilding
-- the table or touching existing data.

DO $$
BEGIN
    IF EXISTS (
        SELECT 1
          FROM information_schema.tables
         WHERE table_schema = 'public'
           AND table_name = 'decision_orchestration_runs'
    ) THEN
        ALTER TABLE decision_orchestration_runs
            DROP CONSTRAINT IF EXISTS decision_orchestration_runs_source_type_chk;

        ALTER TABLE decision_orchestration_runs
            ADD CONSTRAINT decision_orchestration_runs_source_type_chk CHECK (
                source_type IN (
                    'control_room_item',
                    'agent_alert',
                    'intelligence_signal',
                    'monte_carlo_simulation',
                    'calibration_observation',
                    'wisdom_bit',
                    'manual_fixture'
                )
            );
    END IF;
END $$;

INSERT INTO schema_migrations (filename, applied_at)
VALUES ('99zb_decision_orchestrator_wisdom_bit_source.sql', NOW())
ON CONFLICT (filename) DO NOTHING;
