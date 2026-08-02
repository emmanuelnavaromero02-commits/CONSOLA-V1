-- Server-owned Gold provenance binding and atomic gold_refresh finalization.
BEGIN;

DO $$
DECLARE pw text := current_setting('app.omega_outcome_binder_password',true);
BEGIN
  IF NOT EXISTS(SELECT 1 FROM pg_roles WHERE rolname='omega_outcome_binder') THEN
    CREATE ROLE omega_outcome_binder NOLOGIN NOBYPASSRLS;
  END IF;
  IF COALESCE(pw,'')<>'' THEN
    EXECUTE format('ALTER ROLE omega_outcome_binder LOGIN PASSWORD %L',pw);
  END IF;
END $$;
GRANT CONNECT ON DATABASE modecissions TO omega_outcome_binder;
GRANT USAGE ON SCHEMA public TO omega_outcome_binder;

ALTER TABLE intelligence_runs DROP CONSTRAINT IF EXISTS intelligence_runs_status_chk;
ALTER TABLE intelligence_runs ADD CONSTRAINT intelligence_runs_status_chk
  CHECK(status IN ('running','binding_pending','completed','failed','not_ready'));
CREATE UNIQUE INDEX IF NOT EXISTS pipeline_runs_scope_run_binding_key
  ON pipeline_runs(tenant_id,workspace_id,run_id);
CREATE UNIQUE INDEX IF NOT EXISTS intelligence_runs_scope_ref_binding_key
  ON intelligence_runs(tenant_id,workspace_id,id,run_ref);

CREATE TABLE IF NOT EXISTS operational_outcome_binding_candidates(
  tenant_id uuid NOT NULL,
  workspace_id uuid NOT NULL,
  expected_run_id text NOT NULL,
  expected_run_ref text NOT NULL,
  intelligence_run_id bigint NOT NULL,
  dataset text NOT NULL CHECK(dataset~'^[A-Za-z_][A-Za-z0-9_]{0,127}$'),
  materialization_run_id uuid NOT NULL,
  receipt_id uuid NOT NULL,
  head_generation bigint NOT NULL CHECK(head_generation>0),
  object_version text NOT NULL,
  object_checksum text NOT NULL CHECK(object_checksum~'^[0-9a-f]{64}$'),
  schema_digest text NOT NULL CHECK(schema_digest~'^[0-9a-f]{64}$'),
  evidence_digest text NOT NULL CHECK(evidence_digest~'^[0-9a-f]{64}$'),
  authority_digest text NOT NULL CHECK(authority_digest~'^[0-9a-f]{64}$'),
  created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  PRIMARY KEY(tenant_id,workspace_id,expected_run_id,dataset),
  FOREIGN KEY(tenant_id,workspace_id,expected_run_id)
    REFERENCES pipeline_runs(tenant_id,workspace_id,run_id) ON DELETE RESTRICT,
  FOREIGN KEY(tenant_id,workspace_id,intelligence_run_id,expected_run_ref)
    REFERENCES intelligence_runs(tenant_id,workspace_id,id,run_ref) ON DELETE RESTRICT
);

CREATE TABLE IF NOT EXISTS operational_outcome_bindings(
  LIKE operational_outcome_binding_candidates INCLUDING CONSTRAINTS
);
ALTER TABLE operational_outcome_bindings
  ADD COLUMN IF NOT EXISTS object_version text,
  ADD COLUMN IF NOT EXISTS schema_digest text
    CHECK(schema_digest~'^[0-9a-f]{64}$'),
  ADD COLUMN IF NOT EXISTS authority_digest text
    CHECK(authority_digest~'^[0-9a-f]{64}$'),
  ADD COLUMN IF NOT EXISTS outcome_digest text
    CHECK(outcome_digest~'^[0-9a-f]{64}$');
DO $$ BEGIN
  IF EXISTS(SELECT 1 FROM operational_outcome_bindings WHERE object_version IS NULL
    OR schema_digest IS NULL OR authority_digest IS NULL OR outcome_digest IS NULL) THEN
    RAISE EXCEPTION 'existing operational bindings require audited upgrade';
  END IF;
  IF NOT EXISTS(SELECT 1 FROM pg_constraint WHERE conrelid=
      'operational_outcome_bindings'::regclass AND contype='p') THEN
    ALTER TABLE operational_outcome_bindings ADD PRIMARY KEY(
      tenant_id,workspace_id,expected_run_id,dataset
    );
  END IF;
END $$;
ALTER TABLE operational_outcome_bindings
  ALTER COLUMN object_version SET NOT NULL,
  ALTER COLUMN schema_digest SET NOT NULL,
  ALTER COLUMN authority_digest SET NOT NULL,
  ALTER COLUMN outcome_digest SET NOT NULL;

ALTER TABLE operational_outcome_binding_candidates ENABLE ROW LEVEL SECURITY;
ALTER TABLE operational_outcome_binding_candidates FORCE ROW LEVEL SECURITY;
ALTER TABLE operational_outcome_bindings ENABLE ROW LEVEL SECURITY;
ALTER TABLE operational_outcome_bindings FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS operational_binding_candidates_scope
  ON operational_outcome_binding_candidates;
CREATE POLICY operational_binding_candidates_scope
  ON operational_outcome_binding_candidates TO omega_outcome_binder
  USING(omega_rls_workspace_matches(tenant_id,workspace_id))
  WITH CHECK(omega_rls_workspace_matches(tenant_id,workspace_id));
DROP POLICY IF EXISTS operational_outcome_bindings_scope
  ON operational_outcome_bindings;
CREATE POLICY operational_outcome_bindings_scope
  ON operational_outcome_bindings FOR SELECT TO omega_console
  USING(omega_rls_workspace_matches(tenant_id,workspace_id));
REVOKE ALL ON operational_outcome_binding_candidates,
  operational_outcome_bindings FROM PUBLIC;
GRANT SELECT ON operational_outcome_bindings TO omega_console;

DROP FUNCTION IF EXISTS record_operational_outcome_binding(text,text,bigint,jsonb);
CREATE OR REPLACE FUNCTION stage_operational_outcome_binding(
  p_expected_run_id text,p_expected_run_ref text,p_intelligence_run_id bigint,
  p_dataset text,p_materialization_run_id uuid,p_receipt_id uuid,
  p_generation bigint,p_object_version text,p_object_checksum text,
  p_schema_digest text,p_evidence_digest text
) RETURNS text LANGUAGE plpgsql SECURITY DEFINER
SET search_path=pg_catalog,public AS $$
DECLARE
  tenant uuid := NULLIF(current_setting('app.tenant_id',true),'')::uuid;
  workspace uuid := NULLIF(current_setting('app.workspace_id',true),'')::uuid;
  digest_value text;
BEGIN
  IF session_user<>'omega_outcome_binder' OR tenant IS NULL OR workspace IS NULL
     OR p_dataset!~'^[A-Za-z_][A-Za-z0-9_]{0,127}$'
     OR COALESCE(p_object_version,'')=''
     OR p_object_checksum!~'^[0-9a-f]{64}$'
     OR p_schema_digest!~'^[0-9a-f]{64}$'
     OR p_evidence_digest!~'^[0-9a-f]{64}$' OR p_generation<1 THEN
    RAISE EXCEPTION 'operational binding authority is incomplete'
      USING ERRCODE='42501';
  END IF;
  IF NOT EXISTS(SELECT 1 FROM pipeline_runs p WHERE p.tenant_id=tenant
      AND p.workspace_id=workspace AND p.run_id=p_expected_run_id
      AND p.status IN ('running','success'))
     OR NOT EXISTS(SELECT 1 FROM intelligence_runs i WHERE i.tenant_id=tenant
      AND i.workspace_id=workspace AND i.id=p_intelligence_run_id
      AND i.run_ref=p_expected_run_ref AND i.run_mode='gold_refresh'
      AND i.status IN ('running','binding_pending')) THEN
    RAISE EXCEPTION 'operational binding run mismatch' USING ERRCODE='42501';
  END IF;
  digest_value:=encode(digest(convert_to(jsonb_build_object(
    'run',p_expected_run_id,'run_ref',p_expected_run_ref,
    'intelligence_run_id',p_intelligence_run_id,'dataset',p_dataset,
    'materialization_run_id',p_materialization_run_id,'receipt_id',p_receipt_id,
    'generation',p_generation,'object_version',p_object_version,
    'object_checksum',p_object_checksum,'schema_digest',p_schema_digest,
    'evidence_digest',p_evidence_digest)::text,'UTF8'),'sha256'),'hex');
  INSERT INTO operational_outcome_binding_candidates(
    tenant_id,workspace_id,expected_run_id,expected_run_ref,intelligence_run_id,
    dataset,materialization_run_id,receipt_id,head_generation,object_version,
    object_checksum,schema_digest,evidence_digest,authority_digest
  ) VALUES(tenant,workspace,p_expected_run_id,p_expected_run_ref,
    p_intelligence_run_id,p_dataset,p_materialization_run_id,p_receipt_id,
    p_generation,p_object_version,p_object_checksum,p_schema_digest,
    p_evidence_digest,digest_value)
  ON CONFLICT(tenant_id,workspace_id,expected_run_id,dataset) DO UPDATE SET
    expected_run_ref=EXCLUDED.expected_run_ref,
    intelligence_run_id=EXCLUDED.intelligence_run_id,
    materialization_run_id=EXCLUDED.materialization_run_id,
    receipt_id=EXCLUDED.receipt_id,
    head_generation=EXCLUDED.head_generation,
    object_version=EXCLUDED.object_version,
    object_checksum=EXCLUDED.object_checksum,
    schema_digest=EXCLUDED.schema_digest,
    evidence_digest=EXCLUDED.evidence_digest,
    authority_digest=EXCLUDED.authority_digest,
    created_at=clock_timestamp()
  WHERE NOT EXISTS(SELECT 1 FROM operational_outcome_bindings b
          WHERE b.tenant_id=tenant AND b.workspace_id=workspace
            AND b.expected_run_id=p_expected_run_id AND b.dataset=p_dataset)
    AND EXISTS(SELECT 1 FROM intelligence_runs i
          WHERE i.tenant_id=tenant AND i.workspace_id=workspace
            AND i.id=p_intelligence_run_id AND i.run_ref=p_expected_run_ref
            AND i.status='running' AND i.signals_generated=0
            AND COALESCE((i.metadata->>'gold_refresh_retry')::boolean,false))
    AND NOT EXISTS(SELECT 1 FROM decision_intelligence_snapshots s
          WHERE s.workspace_id=workspace
            AND s.intelligence_run_id=p_intelligence_run_id);
  IF NOT EXISTS(SELECT 1 FROM operational_outcome_binding_candidates c
    WHERE c.tenant_id=tenant AND c.workspace_id=workspace
      AND c.expected_run_id=p_expected_run_id AND c.dataset=p_dataset
      AND c.authority_digest=digest_value) THEN
    RAISE EXCEPTION 'operational binding replay mismatch' USING ERRCODE='23505';
  END IF;
  RETURN digest_value;
END $$;

CREATE OR REPLACE FUNCTION finalize_operational_outcome_binding(
  p_expected_run_id text,p_expected_run_ref text,p_intelligence_run_id bigint
) RETURNS text LANGUAGE plpgsql SECURITY DEFINER
SET search_path=pg_catalog,public AS $$
DECLARE
  tenant uuid := NULLIF(current_setting('app.tenant_id',true),'')::uuid;
  workspace uuid := NULLIF(current_setting('app.workspace_id',true),'')::uuid;
  intel intelligence_runs%ROWTYPE;
  expected text[];
  actual text[];
  digest_value text;
BEGIN
  SELECT * INTO intel FROM intelligence_runs i WHERE i.tenant_id=tenant
    AND i.workspace_id=workspace AND i.id=p_intelligence_run_id
    AND i.run_ref=p_expected_run_ref FOR UPDATE;
  SELECT array_agg(DISTINCT value ORDER BY value) INTO expected
    FROM jsonb_array_elements_text(COALESCE(intel.request->'datasets','[]')) value;
  SELECT array_agg(dataset ORDER BY dataset) INTO actual
    FROM operational_outcome_binding_candidates c WHERE c.tenant_id=tenant
      AND c.workspace_id=workspace AND c.expected_run_id=p_expected_run_id
      AND c.expected_run_ref=p_expected_run_ref
      AND c.intelligence_run_id=p_intelligence_run_id;
  IF intel.status='completed' THEN
    SELECT min(outcome_digest) INTO digest_value FROM operational_outcome_bindings b
     WHERE b.tenant_id=tenant AND b.workspace_id=workspace
       AND b.expected_run_id=p_expected_run_id AND b.intelligence_run_id=intel.id;
    IF digest_value IS NULL THEN
      RAISE EXCEPTION 'completed gold refresh lacks binding' USING ERRCODE='23514';
    END IF;
    RETURN digest_value;
  END IF;
  IF intel.run_mode<>'gold_refresh' OR intel.status<>'binding_pending'
     OR intel.signals_generated<1 OR expected IS DISTINCT FROM actual THEN
    RAISE EXCEPTION 'operational outcome binding mismatch' USING ERRCODE='23514';
  END IF;
  digest_value:=encode(digest(convert_to(jsonb_build_object(
    'run',p_expected_run_id,'run_ref',p_expected_run_ref,'intelligence',intel.id,
    'signals',intel.signals_generated,'authority',(
      SELECT jsonb_agg(authority_digest ORDER BY dataset)
      FROM operational_outcome_binding_candidates c WHERE c.tenant_id=tenant
       AND c.workspace_id=workspace AND c.expected_run_id=p_expected_run_id
    ))::text,'UTF8'),'sha256'),'hex');
  INSERT INTO operational_outcome_bindings SELECT c.*,digest_value
    FROM operational_outcome_binding_candidates c WHERE c.tenant_id=tenant
      AND c.workspace_id=workspace AND c.expected_run_id=p_expected_run_id
  ON CONFLICT DO NOTHING;
  UPDATE intelligence_runs SET status='completed',completed_at=clock_timestamp(),
    updated_at=clock_timestamp() WHERE id=intel.id;
  RETURN digest_value;
END $$;

ALTER FUNCTION stage_operational_outcome_binding(
  text,text,bigint,text,uuid,uuid,bigint,text,text,text,text) OWNER TO postgres;
ALTER FUNCTION finalize_operational_outcome_binding(text,text,bigint) OWNER TO postgres;
REVOKE ALL ON FUNCTION stage_operational_outcome_binding(
  text,text,bigint,text,uuid,uuid,bigint,text,text,text,text
) FROM PUBLIC,omega_console;
GRANT EXECUTE ON FUNCTION stage_operational_outcome_binding(
  text,text,bigint,text,uuid,uuid,bigint,text,text,text,text
) TO omega_outcome_binder;
REVOKE ALL ON FUNCTION finalize_operational_outcome_binding(text,text,bigint)
  FROM PUBLIC;
GRANT EXECUTE ON FUNCTION finalize_operational_outcome_binding(text,text,bigint)
  TO omega_console;
ALTER ROLE omega_console NOBYPASSRLS;
ALTER ROLE omega_outcome_binder NOBYPASSRLS;

INSERT INTO schema_migrations(filename,applied_at)
VALUES('99zzr_operational_outcome_binding.sql',clock_timestamp())
ON CONFLICT(filename) DO NOTHING;
COMMIT;
