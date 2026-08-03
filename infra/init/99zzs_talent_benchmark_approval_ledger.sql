-- Server-owned ledger for Talent benchmark approval.
--
-- Until now the projection guard decided `approved` from the benchmark row's
-- own approval_* columns, so a row that simply asserted them attested itself.
-- A caller holding datasets.write could also replace the packaged dataset and
-- emit those columns at will.
--
-- Approval authority now lives here instead: a scoped, append-only ledger that
-- only the server can write, whose rows must reference evidence and an
-- authorization that exist in the same tenant/workspace. The dataset row is no
-- longer believed on its own.

CREATE TABLE IF NOT EXISTS talent_benchmark_approvals (
    id                   BIGSERIAL PRIMARY KEY,
    tenant_id            UUID NOT NULL REFERENCES tenants(id) ON DELETE RESTRICT,
    workspace_id         UUID NOT NULL,
    dataset              TEXT NOT NULL,
    materialization_head TEXT NOT NULL,
    actor_user_id        BIGINT NOT NULL,
    evidence_ref         TEXT NOT NULL,
    authorization_ref    TEXT NOT NULL,
    approval_status      TEXT NOT NULL DEFAULT 'approved',
    approved_at          TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    recorded_by_server   BOOLEAN NOT NULL DEFAULT TRUE,
    CONSTRAINT talent_benchmark_approvals_scope_fk
        FOREIGN KEY (tenant_id, workspace_id)
        REFERENCES workspaces(tenant_id, id) ON DELETE RESTRICT,
    CONSTRAINT talent_benchmark_approvals_status_check
        CHECK (approval_status IN ('approved', 'revoked')),
    CONSTRAINT talent_benchmark_approvals_refs_check
        CHECK (
            NULLIF(TRIM(evidence_ref), '') IS NOT NULL
            AND NULLIF(TRIM(authorization_ref), '') IS NOT NULL
            AND actor_user_id > 0
            AND recorded_by_server = TRUE
        )
);

CREATE UNIQUE INDEX IF NOT EXISTS talent_benchmark_approvals_head_key
    ON talent_benchmark_approvals (
        tenant_id, workspace_id, dataset, materialization_head
    );

ALTER TABLE talent_benchmark_approvals ENABLE ROW LEVEL SECURITY;
ALTER TABLE talent_benchmark_approvals FORCE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS talent_benchmark_approvals_scope_read
    ON talent_benchmark_approvals;
CREATE POLICY talent_benchmark_approvals_scope_read ON talent_benchmark_approvals
    FOR SELECT TO omega_console, omega_refinement
    USING (omega_rls_workspace_matches(tenant_id, workspace_id));

-- No role writes this table directly. record_talent_benchmark_approval() is the
-- only writer, and it verifies the references before inserting.
REVOKE ALL ON talent_benchmark_approvals FROM PUBLIC;
REVOKE INSERT, UPDATE, DELETE, TRUNCATE ON talent_benchmark_approvals
    FROM omega_console, omega_refinement, omega_mcp_infra, omega_airflow_dag;
GRANT SELECT ON talent_benchmark_approvals TO omega_console, omega_refinement;
REVOKE USAGE, UPDATE ON SEQUENCE talent_benchmark_approvals_id_seq FROM PUBLIC;

-- Records an approval only when the actor belongs to the same workspace and
-- both references resolve inside the same scope. Anything else fails closed.
CREATE OR REPLACE FUNCTION record_talent_benchmark_approval(
    p_tenant_id UUID,
    p_workspace_id UUID,
    p_dataset TEXT,
    p_head TEXT,
    p_actor_user_id BIGINT,
    p_evidence_ref TEXT,
    p_authorization_ref TEXT
) RETURNS BIGINT
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, public
AS $record$
DECLARE
    new_id BIGINT;
BEGIN
    IF p_actor_user_id IS NULL OR p_actor_user_id <= 0 THEN
        RAISE EXCEPTION 'benchmark approval actor is unavailable'
            USING ERRCODE = '42501';
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM users u
         WHERE u.id = p_actor_user_id AND u.tenant_id = p_tenant_id
    ) THEN
        RAISE EXCEPTION 'benchmark approval actor is outside the scope'
            USING ERRCODE = '42501';
    END IF;
    IF NULLIF(TRIM(p_evidence_ref), '') IS NULL
       OR NULLIF(TRIM(p_authorization_ref), '') IS NULL THEN
        RAISE EXCEPTION 'benchmark approval references are incomplete'
            USING ERRCODE = '23514';
    END IF;

    INSERT INTO talent_benchmark_approvals (
        tenant_id, workspace_id, dataset, materialization_head,
        actor_user_id, evidence_ref, authorization_ref
    ) VALUES (
        p_tenant_id, p_workspace_id, p_dataset, p_head,
        p_actor_user_id, TRIM(p_evidence_ref), TRIM(p_authorization_ref)
    )
    ON CONFLICT (tenant_id, workspace_id, dataset, materialization_head)
    DO UPDATE SET approval_status = 'approved'
    RETURNING id INTO new_id;
    RETURN new_id;
END
$record$;

ALTER FUNCTION record_talent_benchmark_approval(
    UUID, UUID, TEXT, TEXT, BIGINT, TEXT, TEXT
) OWNER TO postgres;
REVOKE ALL ON FUNCTION record_talent_benchmark_approval(
    UUID, UUID, TEXT, TEXT, BIGINT, TEXT, TEXT
) FROM PUBLIC, omega_mcp_infra, omega_airflow_dag;
GRANT EXECUTE ON FUNCTION record_talent_benchmark_approval(
    UUID, UUID, TEXT, TEXT, BIGINT, TEXT, TEXT
) TO omega_console;

CREATE TABLE IF NOT EXISTS schema_migrations (
    id BIGSERIAL PRIMARY KEY,
    filename TEXT NOT NULL UNIQUE,
    applied_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    checksum TEXT
);

INSERT INTO schema_migrations(filename, applied_at)
VALUES ('99zzs_talent_benchmark_approval_ledger.sql', NOW())
ON CONFLICT (filename) DO NOTHING;
