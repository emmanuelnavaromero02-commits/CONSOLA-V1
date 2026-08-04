-- Scoped, server-owned authority for the packaged Talent benchmark.
-- Dataset rows never attest approval.  This ledger records a checker decision
-- only after the database resolves scope, actor, role and bound evidence.

CREATE UNIQUE INDEX IF NOT EXISTS evidence_packs_scope_id_key
    ON evidence_packs (tenant_id, workspace_id, id);

DO $scope_fk$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
         WHERE conname = 'evidence_items_scope_pack_fk'
           AND conrelid = 'evidence_items'::regclass
    ) THEN
        ALTER TABLE evidence_items
            ADD CONSTRAINT evidence_items_scope_pack_fk
            FOREIGN KEY (tenant_id, workspace_id, evidence_pack_id)
            REFERENCES evidence_packs(tenant_id, workspace_id, id)
            ON DELETE RESTRICT NOT VALID;
        ALTER TABLE evidence_items
            VALIDATE CONSTRAINT evidence_items_scope_pack_fk;
    END IF;
END
$scope_fk$;

CREATE TABLE IF NOT EXISTS talent_benchmark_approvals (
    id                   BIGSERIAL PRIMARY KEY,
    tenant_id            UUID NOT NULL,
    workspace_id         UUID NOT NULL,
    dataset              TEXT NOT NULL,
    materialization_head UUID NOT NULL,
    actor_user_id        BIGINT NOT NULL,
    maker_user_id        BIGINT NOT NULL,
    evidence_pack_id     BIGINT NOT NULL,
    authorization_role_id INTEGER NOT NULL,
    authorization_grant_created_at TIMESTAMPTZ NOT NULL,
    authorization_ref    UUID NOT NULL UNIQUE DEFAULT gen_random_uuid(),
    evidence_digest      CHAR(64) NOT NULL,
    evidence_digest_version SMALLINT NOT NULL DEFAULT 1,
    evidence_item_count  BIGINT NOT NULL,
    authorization_digest CHAR(64) NOT NULL,
    approval_status      TEXT NOT NULL DEFAULT 'approved',
    approved_at          TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    recorded_by_server   BOOLEAN NOT NULL DEFAULT TRUE,
    CONSTRAINT talent_benchmark_approvals_scope_fk
        FOREIGN KEY (tenant_id, workspace_id)
        REFERENCES workspaces(tenant_id, id) ON DELETE RESTRICT,
    CONSTRAINT talent_benchmark_approvals_evidence_fk
        FOREIGN KEY (tenant_id, workspace_id, evidence_pack_id)
        REFERENCES evidence_packs(tenant_id, workspace_id, id) ON DELETE RESTRICT,
    CONSTRAINT talent_benchmark_approvals_actor_fk
        FOREIGN KEY (actor_user_id) REFERENCES users(id) ON DELETE RESTRICT,
    CONSTRAINT talent_benchmark_approvals_maker_fk
        FOREIGN KEY (maker_user_id) REFERENCES users(id) ON DELETE RESTRICT,
    CONSTRAINT talent_benchmark_approvals_role_fk
        FOREIGN KEY (authorization_role_id) REFERENCES roles(id) ON DELETE RESTRICT,
    CONSTRAINT talent_benchmark_approvals_dataset_check
        CHECK (dataset = 'sap_successfactors_talent_benchmark_internal'),
    CONSTRAINT talent_benchmark_approvals_status_check
        CHECK (approval_status = 'approved'),
    CONSTRAINT talent_benchmark_approvals_separation_check
        CHECK (actor_user_id > 0 AND maker_user_id > 0
               AND actor_user_id <> maker_user_id
               AND recorded_by_server = TRUE),
    CONSTRAINT talent_benchmark_approvals_digest_check
        CHECK (evidence_digest ~ '^[0-9a-f]{64}$'
               AND authorization_digest ~ '^[0-9a-f]{64}$'
               AND evidence_digest_version = 1
               AND evidence_item_count > 0)
);

ALTER TABLE talent_benchmark_approvals
    ADD COLUMN IF NOT EXISTS evidence_digest_version SMALLINT NOT NULL DEFAULT 1;
ALTER TABLE talent_benchmark_approvals
    ADD COLUMN IF NOT EXISTS evidence_item_count BIGINT;

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

REVOKE ALL ON talent_benchmark_approvals FROM PUBLIC;
REVOKE INSERT, UPDATE, DELETE, TRUNCATE ON talent_benchmark_approvals
    FROM omega_console, omega_refinement, omega_mcp_infra, omega_airflow_dag;
GRANT SELECT ON talent_benchmark_approvals TO omega_console, omega_refinement;
REVOKE ALL ON SEQUENCE talent_benchmark_approvals_id_seq FROM PUBLIC;

CREATE OR REPLACE FUNCTION record_talent_benchmark_approval(
    p_materialization_head UUID,
    p_evidence_pack_id BIGINT
) RETURNS BIGINT
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, public
AS $record$
DECLARE
    scoped_tenant UUID;
    scoped_workspace UUID;
    scoped_actor BIGINT;
    evidence_owner BIGINT;
    evidence_signal TEXT;
    evidence_summary TEXT;
    evidence_confidence NUMERIC(5,2);
    evidence_metadata JSONB;
    evidence_created_at TIMESTAMPTZ;
    evidence_items_document JSONB;
    evidence_item_count BIGINT;
    evidence_total_item_count BIGINT;
    approver_role_id INTEGER;
    grant_created_at TIMESTAMPTZ;
    resolved_evidence_digest TEXT;
    resolved_authorization_digest TEXT;
    new_id BIGINT;
BEGIN
    scoped_tenant := NULLIF(current_setting('app.tenant_id', TRUE), '')::UUID;
    scoped_workspace := NULLIF(current_setting('app.workspace_id', TRUE), '')::UUID;
    scoped_actor := NULLIF(current_setting('app.user_id', TRUE), '')::BIGINT;
    IF scoped_tenant IS NULL OR scoped_workspace IS NULL OR scoped_actor IS NULL THEN
        RAISE EXCEPTION 'benchmark approval authority is unavailable'
            USING ERRCODE = '42501';
    END IF;

    SELECT r.id, uwr.created_at
      INTO approver_role_id, grant_created_at
      FROM users u
          JOIN user_workspace_roles uwr
            ON uwr.user_id = u.id AND uwr.workspace_id = scoped_workspace
          JOIN roles r ON r.id = uwr.role_id
          JOIN workspaces w
            ON w.id = scoped_workspace AND w.tenant_id = scoped_tenant
         WHERE u.id = scoped_actor
           AND u.tenant_id = scoped_tenant
           AND u.is_active = TRUE
           AND r.name = 'control_room_approver'
     FOR SHARE OF u, uwr, r;
    IF approver_role_id IS NULL OR grant_created_at IS NULL THEN
        RAISE EXCEPTION 'benchmark approval is not authorized'
            USING ERRCODE = '42501';
    END IF;

    SELECT e.owner_user_id, e.signal_id, e.summary, e.confidence,
           e.metadata, e.created_at
      INTO evidence_owner, evidence_signal, evidence_summary,
           evidence_confidence, evidence_metadata, evidence_created_at
      FROM evidence_packs e
     WHERE e.id = p_evidence_pack_id
       AND e.tenant_id = scoped_tenant
       AND e.workspace_id = scoped_workspace
     FOR UPDATE;
    IF evidence_owner IS NULL OR evidence_owner = scoped_actor
       OR COALESCE(evidence_metadata->>'purpose', '')
            <> 'talent_benchmark_approval'
       OR COALESCE(evidence_metadata->>'dataset', '')
            <> 'sap_successfactors_talent_benchmark_internal'
       OR COALESCE(evidence_metadata->>'materialization_head', '')
            <> p_materialization_head::TEXT THEN
        RAISE EXCEPTION 'benchmark approval evidence is invalid'
            USING ERRCODE = '42501';
    END IF;

    SELECT COUNT(*),
           COUNT(*) FILTER (
               WHERE i.tenant_id = scoped_tenant
                 AND i.workspace_id = scoped_workspace
           ),
           jsonb_agg(
               jsonb_build_object(
                   'id', i.id,
                   'tenant_id', i.tenant_id,
                   'workspace_id', i.workspace_id,
                   'source_type', i.source_type,
                   'source_ref', i.source_ref,
                   'query_text', i.query_text,
                   'data', i.data,
                   'supports_hypothesis', i.supports_hypothesis,
                   'strength', i.strength,
                   'metadata', i.metadata,
                   'owner_user_id', i.owner_user_id,
                   'created_at', i.created_at
               ) ORDER BY i.id
           ) FILTER (
               WHERE i.tenant_id = scoped_tenant
                 AND i.workspace_id = scoped_workspace
           )
      INTO evidence_total_item_count, evidence_item_count,
           evidence_items_document
      FROM evidence_items i
     WHERE i.evidence_pack_id = p_evidence_pack_id;
    IF evidence_item_count = 0
       OR evidence_total_item_count <> evidence_item_count THEN
        RAISE EXCEPTION 'benchmark approval evidence is invalid'
            USING ERRCODE = '42501';
    END IF;

    resolved_evidence_digest := encode(digest(
        jsonb_build_object(
            'version', 1,
            'tenant_id', scoped_tenant,
            'workspace_id', scoped_workspace,
            'evidence_pack_id', p_evidence_pack_id,
            'owner_user_id', evidence_owner,
            'signal_id', evidence_signal,
            'summary', evidence_summary,
            'confidence', evidence_confidence,
            'metadata', evidence_metadata,
            'created_at', evidence_created_at,
            'items', evidence_items_document
        )::TEXT,
        'sha256'
    ), 'hex');
    resolved_authorization_digest := encode(digest(
        concat_ws('|', scoped_tenant::TEXT, scoped_workspace::TEXT,
                  scoped_actor::TEXT, approver_role_id::TEXT,
                  grant_created_at::TEXT, 'talent_benchmark_approve'),
        'sha256'
    ), 'hex');

    INSERT INTO talent_benchmark_approvals (
        tenant_id, workspace_id, dataset, materialization_head,
        actor_user_id, maker_user_id, evidence_pack_id,
        authorization_role_id, authorization_grant_created_at,
        evidence_digest, evidence_digest_version, evidence_item_count,
        authorization_digest
    ) VALUES (
        scoped_tenant, scoped_workspace,
        'sap_successfactors_talent_benchmark_internal', p_materialization_head,
        scoped_actor, evidence_owner, p_evidence_pack_id,
        approver_role_id, grant_created_at,
        resolved_evidence_digest, 1, evidence_item_count,
        resolved_authorization_digest
    ) RETURNING id INTO new_id;
    RETURN new_id;
END
$record$;

ALTER FUNCTION record_talent_benchmark_approval(UUID, BIGINT) OWNER TO postgres;
REVOKE ALL ON FUNCTION record_talent_benchmark_approval(UUID, BIGINT)
    FROM PUBLIC, omega_mcp_infra, omega_airflow_dag, omega_refinement;
GRANT EXECUTE ON FUNCTION record_talent_benchmark_approval(UUID, BIGINT)
    TO omega_console;

CREATE OR REPLACE FUNCTION protect_talent_benchmark_approval_items()
RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, public
AS $protect_items$
DECLARE
    pack_id BIGINT;
BEGIN
    FOR pack_id IN
        SELECT DISTINCT candidate
          FROM unnest(ARRAY[
              CASE WHEN TG_OP <> 'INSERT' THEN OLD.evidence_pack_id END,
              CASE WHEN TG_OP <> 'DELETE' THEN NEW.evidence_pack_id END
          ]) AS candidate
         WHERE candidate IS NOT NULL
         ORDER BY candidate
    LOOP
        PERFORM 1 FROM evidence_packs WHERE id = pack_id FOR UPDATE;
        IF NOT FOUND THEN
            RAISE EXCEPTION 'benchmark evidence pack is unavailable'
                USING ERRCODE = '23503';
        END IF;
        IF EXISTS (
            SELECT 1 FROM talent_benchmark_approvals approval
             WHERE approval.evidence_pack_id = pack_id
        ) THEN
            RAISE EXCEPTION 'approved benchmark evidence is immutable'
                USING ERRCODE = '23503';
        END IF;
    END LOOP;
    IF TG_OP = 'DELETE' THEN
        RETURN OLD;
    END IF;
    RETURN NEW;
END
$protect_items$;

ALTER FUNCTION protect_talent_benchmark_approval_items() OWNER TO postgres;
REVOKE ALL ON FUNCTION protect_talent_benchmark_approval_items() FROM PUBLIC;
DROP TRIGGER IF EXISTS protect_talent_benchmark_approval_items
    ON evidence_items;
CREATE TRIGGER protect_talent_benchmark_approval_items
    BEFORE INSERT OR UPDATE OR DELETE ON evidence_items
    FOR EACH ROW EXECUTE FUNCTION protect_talent_benchmark_approval_items();

CREATE OR REPLACE FUNCTION protect_talent_benchmark_approval_evidence()
RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, public
AS $protect$
BEGIN
    IF EXISTS (
        SELECT 1 FROM talent_benchmark_approvals approval
         WHERE approval.tenant_id = OLD.tenant_id
           AND approval.workspace_id = OLD.workspace_id
           AND approval.evidence_pack_id = OLD.id
    ) THEN
        RAISE EXCEPTION 'approved benchmark evidence is immutable'
            USING ERRCODE = '23503';
    END IF;
    IF TG_OP = 'DELETE' THEN
        RETURN OLD;
    END IF;
    RETURN NEW;
END
$protect$;

ALTER FUNCTION protect_talent_benchmark_approval_evidence() OWNER TO postgres;
REVOKE ALL ON FUNCTION protect_talent_benchmark_approval_evidence() FROM PUBLIC;
DROP TRIGGER IF EXISTS protect_talent_benchmark_approval_evidence
    ON evidence_packs;
CREATE TRIGGER protect_talent_benchmark_approval_evidence
    BEFORE UPDATE OR DELETE ON evidence_packs
    FOR EACH ROW EXECUTE FUNCTION protect_talent_benchmark_approval_evidence();

CREATE TABLE IF NOT EXISTS schema_migrations (
    id BIGSERIAL PRIMARY KEY,
    filename TEXT NOT NULL UNIQUE,
    applied_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    checksum TEXT
);
INSERT INTO schema_migrations(filename, applied_at)
VALUES ('99zzs_talent_benchmark_approval_ledger.sql', NOW())
ON CONFLICT (filename) DO NOTHING;
