-- Mission 4: shared memory between agents.
--
-- One agent records what it found (a data gap, an error, an insight, a
-- warning); any other agent in the same workspace can read it before acting on
-- the same subject. The canonical case this exists for: Finance discovers that
-- cost_center_budget has no Gold table behind it (docs/data_gaps.md), records
-- the gap once, and Risk reads it before trying cost_center_overrun, which is
-- unsupported for exactly the same reason.
--
-- Advisory only. Nothing here approves, executes, or writes back to an
-- external system. A finding is a note for another agent, never an action.
--
-- Scope is a database invariant, not an API convention: every row carries
-- tenant_id + workspace_id, native RLS matches them against app.tenant_id /
-- app.workspace_id through omega_rls_workspace_matches(), and DELETE is
-- revoked from every service role so an agent cannot erase what another agent
-- recorded. Findings age out through expires_at instead.

CREATE TABLE IF NOT EXISTS agent_shared_findings (
    id              BIGSERIAL PRIMARY KEY,
    tenant_id       UUID NOT NULL,
    workspace_id    UUID NOT NULL,
    -- Who recorded it. CASCADE because a finding attributed to a deleted agent
    -- can no longer be trusted or followed up.
    agent_id        UUID NOT NULL REFERENCES agents(id) ON DELETE CASCADE,
    finding_type    TEXT NOT NULL,
    -- What the finding is about: a dataset, a column, a metric or an entity.
    -- Free text on purpose (it spans cartridges) but bounded and trimmed.
    subject         TEXT NOT NULL,
    -- Short business Spanish, written for another agent to read.
    summary         TEXT NOT NULL,
    detail          JSONB NOT NULL DEFAULT '{}'::jsonb,
    severity        TEXT NOT NULL DEFAULT 'medium',
    created_at      TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    -- NULL means the finding does not age out on its own.
    expires_at      TIMESTAMPTZ,
    CONSTRAINT agent_shared_findings_scope_fk
        FOREIGN KEY (tenant_id, workspace_id)
        REFERENCES workspaces(tenant_id, id) ON DELETE CASCADE,
    CONSTRAINT agent_shared_findings_type_check
        CHECK (finding_type IN ('data_gap', 'error', 'insight', 'warning')),
    CONSTRAINT agent_shared_findings_severity_check
        CHECK (severity IN ('critical', 'high', 'medium', 'low')),
    CONSTRAINT agent_shared_findings_subject_check
        CHECK (subject = btrim(subject) AND length(subject) BETWEEN 1 AND 200),
    CONSTRAINT agent_shared_findings_summary_check
        CHECK (length(btrim(summary)) BETWEEN 1 AND 1000),
    CONSTRAINT agent_shared_findings_detail_check
        CHECK (jsonb_typeof(detail) = 'object')
    -- Deliberately NO "expires_at > created_at" constraint. Because DELETE is
    -- revoked from every service role, setting expires_at into the past is the
    -- ONLY way to retire a finding that turned out to be wrong or stale, and a
    -- constraint on that ordering would forbid exactly that UPDATE. The
    -- insert-time rule "an expiry must be in the future" belongs to the service
    -- (agent_memory._normalise_expiry), which can refuse it and say why instead
    -- of surfacing a CheckViolation as a generic write failure.
);

-- The lookup the service performs on every check: active findings for one
-- subject inside one workspace, newest first.
CREATE INDEX IF NOT EXISTS agent_shared_findings_subject_idx
    ON agent_shared_findings (tenant_id, workspace_id, subject, created_at DESC);

-- Secondary: what a given agent has recorded, used by the record-once guard.
CREATE INDEX IF NOT EXISTS agent_shared_findings_agent_idx
    ON agent_shared_findings (tenant_id, workspace_id, agent_id, created_at DESC);

ALTER TABLE agent_shared_findings ENABLE ROW LEVEL SECURITY;
ALTER TABLE agent_shared_findings FORCE ROW LEVEL SECURITY;

-- omega_mcp_infra is included only where the role exists, matching the guarded
-- grant idiom in 99o_agent_monitor_control_room_alerts.sql: mcp-infra writes
-- this table directly for the agent-memory tool, the same way it writes
-- control_room_items for raise_alert.
DO $agent_shared_findings_policy$
DECLARE
    policy_roles TEXT;
BEGIN
    SELECT string_agg(quote_ident(rolname), ', ' ORDER BY rolname)
      INTO policy_roles
      FROM pg_roles
     WHERE rolname IN ('omega_console', 'omega_workspace', 'omega_mcp_infra');

    IF policy_roles IS NULL THEN
        RAISE NOTICE 'agent_shared_findings: no service role present, policy skipped';
        RETURN;
    END IF;

    EXECUTE 'DROP POLICY IF EXISTS agent_shared_findings_workspace_scope'
            ' ON agent_shared_findings';
    EXECUTE format(
        'CREATE POLICY agent_shared_findings_workspace_scope'
        ' ON agent_shared_findings'
        ' FOR ALL TO %s'
        ' USING (omega_rls_workspace_matches(tenant_id, workspace_id))'
        ' WITH CHECK (omega_rls_workspace_matches(tenant_id, workspace_id))',
        policy_roles
    );
END
$agent_shared_findings_policy$;

REVOKE ALL ON agent_shared_findings FROM PUBLIC;
REVOKE ALL ON SEQUENCE agent_shared_findings_id_seq FROM PUBLIC;

DO $agent_shared_findings_grants$
DECLARE
    role_name TEXT;
BEGIN
    FOREACH role_name IN ARRAY ARRAY['omega_console', 'omega_workspace', 'omega_mcp_infra']
    LOOP
        IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = role_name) THEN
            -- No DELETE and no TRUNCATE: shared memory is append-only for
            -- services. Expiry is a column, not a deletion.
            EXECUTE format(
                'REVOKE DELETE, TRUNCATE ON agent_shared_findings FROM %I',
                role_name
            );
            EXECUTE format(
                'GRANT SELECT, INSERT, UPDATE ON agent_shared_findings TO %I',
                role_name
            );
            EXECUTE format(
                'GRANT USAGE, SELECT ON SEQUENCE agent_shared_findings_id_seq TO %I',
                role_name
            );
        END IF;
    END LOOP;
END
$agent_shared_findings_grants$;

INSERT INTO schema_migrations (filename, applied_at)
VALUES ('99zzzzg_agent_shared_findings.sql', NOW())
ON CONFLICT (filename) DO NOTHING;
