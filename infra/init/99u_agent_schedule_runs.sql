-- v1.45.119 AgentOps: idempotent scheduled monitor windows.
--
-- One logical schedule fire should produce at most one agent execution. Airflow
-- retries, duplicate DAG windows and manual replays all hit the same unique key
-- and reuse the persisted result instead of creating another monitor alert.

CREATE TABLE IF NOT EXISTS agent_schedule_runs (
    id                  BIGSERIAL PRIMARY KEY,
    tenant_id           UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    workspace_id        UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    agent_id            UUID NOT NULL REFERENCES agents(id) ON DELETE CASCADE,
    scheduled_fire_at   TIMESTAMPTZ NOT NULL,
    schedule_key        TEXT NOT NULL DEFAULT 'default',
    airflow_dag_run_id  TEXT,
    status              TEXT NOT NULL DEFAULT 'running',
    agent_run_id        BIGINT REFERENCES agent_runs(id) ON DELETE SET NULL,
    started_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    finished_at         TIMESTAMPTZ,
    error_message       TEXT,
    metadata            JSONB NOT NULL DEFAULT '{}'::jsonb,
    heartbeat_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    lease_expires_at    TIMESTAMPTZ,
    fencing_token       BIGINT NOT NULL DEFAULT 1,
    CONSTRAINT agent_schedule_runs_status_chk
      CHECK (status IN ('running', 'ok', 'error', 'cancelled', 'skipped'))
);

ALTER TABLE agent_schedule_runs
  ADD COLUMN IF NOT EXISTS heartbeat_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  ADD COLUMN IF NOT EXISTS lease_expires_at TIMESTAMPTZ,
  ADD COLUMN IF NOT EXISTS fencing_token BIGINT NOT NULL DEFAULT 1;

CREATE UNIQUE INDEX IF NOT EXISTS agent_schedule_runs_fire_uidx
  ON agent_schedule_runs (agent_id, schedule_key, scheduled_fire_at);

CREATE INDEX IF NOT EXISTS agent_schedule_runs_workspace_idx
  ON agent_schedule_runs (workspace_id, scheduled_fire_at DESC);

CREATE INDEX IF NOT EXISTS agent_schedule_runs_agent_idx
  ON agent_schedule_runs (agent_id, scheduled_fire_at DESC);

CREATE INDEX IF NOT EXISTS agent_schedule_runs_active_lease_idx
  ON agent_schedule_runs (lease_expires_at)
  WHERE status = 'running';

GRANT SELECT, INSERT, UPDATE ON agent_schedule_runs TO omega_console;
GRANT USAGE, SELECT ON SEQUENCE agent_schedule_runs_id_seq TO omega_console;

ALTER TABLE agent_schedule_runs ENABLE ROW LEVEL SECURITY;
ALTER TABLE agent_schedule_runs FORCE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS agent_schedule_runs_console_scope_rls ON agent_schedule_runs;
CREATE POLICY agent_schedule_runs_console_scope_rls ON agent_schedule_runs
  FOR ALL
  TO omega_console
  USING (omega_rls_workspace_matches(tenant_id, workspace_id))
  WITH CHECK (omega_rls_workspace_matches(tenant_id, workspace_id));

INSERT INTO schema_migrations(filename, applied_at)
VALUES ('99u_agent_schedule_runs.sql', NOW())
ON CONFLICT (filename) DO NOTHING;
