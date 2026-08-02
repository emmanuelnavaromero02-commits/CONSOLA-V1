-- Append-only upgrade for scheduled monitor lease and fencing authority.

ALTER TABLE agent_schedule_runs
  ADD COLUMN IF NOT EXISTS heartbeat_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  ADD COLUMN IF NOT EXISTS lease_expires_at TIMESTAMPTZ,
  ADD COLUMN IF NOT EXISTS fencing_token BIGINT NOT NULL DEFAULT 1;

CREATE INDEX IF NOT EXISTS agent_schedule_runs_active_lease_idx
  ON agent_schedule_runs (lease_expires_at)
  WHERE status = 'running';

INSERT INTO schema_migrations(filename,applied_at)
VALUES('99zzp_agent_schedule_effect_leases.sql',clock_timestamp())
ON CONFLICT(filename) DO NOTHING;
