-- v1.45.121 AgentOps: allow advisory Monte Carlo runs sourced by WisdomBits.
--
-- This lets scheduled monitor agents persist reproducible simulations for
-- WB-TALENTO without inventing an intelligence_signal first. Scope and RLS stay
-- unchanged; the application still only accepts the allowlisted WisdomBit.

ALTER TABLE monte_carlo_simulations
    DROP CONSTRAINT IF EXISTS monte_carlo_simulations_source_type_check;

ALTER TABLE monte_carlo_simulations
    ADD CONSTRAINT monte_carlo_simulations_source_type_check
    CHECK (source_type IN (
        'signal',
        'decision_option',
        'manual_fixture',
        'backtest_case',
        'wisdom_bit'
    ));

INSERT INTO schema_migrations (filename, applied_at)
VALUES ('99z_monte_carlo_wisdom_bit_source.sql', NOW())
ON CONFLICT (filename) DO NOTHING;
