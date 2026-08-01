-- Fenced leases prevent crashed dataset refresh slots remaining active forever.

ALTER TABLE pipeline_runs
  ADD COLUMN IF NOT EXISTS heartbeat_at TIMESTAMPTZ,
  ADD COLUMN IF NOT EXISTS lease_expires_at TIMESTAMPTZ,
  ADD COLUMN IF NOT EXISTS fencing_token BIGINT NOT NULL DEFAULT 0;

CREATE INDEX IF NOT EXISTS pipeline_runs_active_lease_idx
  ON pipeline_runs (lease_expires_at)
  WHERE status = 'running';

INSERT INTO schema_migrations(filename, applied_at)
VALUES ('99zza_materialization_run_leases.sql', NOW())
ON CONFLICT (filename) DO NOTHING;
