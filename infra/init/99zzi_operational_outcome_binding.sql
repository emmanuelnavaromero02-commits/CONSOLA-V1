-- Bind the exact published Gold generations read by Intelligence to the
-- scoped pipeline registry and completed Intelligence outcome.
BEGIN;

CREATE UNIQUE INDEX IF NOT EXISTS pipeline_runs_scope_run_binding_key
  ON pipeline_runs(tenant_id,workspace_id,run_id);
CREATE UNIQUE INDEX IF NOT EXISTS intelligence_runs_scope_ref_binding_key
  ON intelligence_runs(tenant_id,workspace_id,id,run_ref);

CREATE TABLE IF NOT EXISTS operational_outcome_bindings (
  tenant_id uuid NOT NULL,
  workspace_id uuid NOT NULL,
  expected_run_id text NOT NULL,
  expected_run_ref text NOT NULL,
  intelligence_run_id bigint NOT NULL,
  run_ref text NOT NULL,
  dataset text NOT NULL CHECK (dataset ~ '^[A-Za-z_][A-Za-z0-9_]{0,127}$'),
  materialization_run_id uuid NOT NULL,
  receipt_id uuid NOT NULL,
  head_generation bigint NOT NULL CHECK (head_generation > 0),
  object_checksum text NOT NULL CHECK (object_checksum ~ '^[0-9a-f]{64}$'),
  evidence_digest text NOT NULL CHECK (evidence_digest ~ '^[0-9a-f]{64}$'),
  outcome_digest text NOT NULL CHECK (outcome_digest ~ '^[0-9a-f]{64}$'),
  created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  PRIMARY KEY (tenant_id,workspace_id,expected_run_id,dataset),
  CHECK (expected_run_ref=run_ref),
  FOREIGN KEY (tenant_id,workspace_id)
    REFERENCES workspaces(tenant_id,id) ON DELETE RESTRICT,
  FOREIGN KEY (tenant_id,workspace_id,expected_run_id)
    REFERENCES pipeline_runs(tenant_id,workspace_id,run_id) ON DELETE RESTRICT,
  FOREIGN KEY (tenant_id,workspace_id,intelligence_run_id,run_ref)
    REFERENCES intelligence_runs(tenant_id,workspace_id,id,run_ref)
    ON DELETE RESTRICT
);

CREATE INDEX IF NOT EXISTS operational_outcome_bindings_intelligence_idx
  ON operational_outcome_bindings(
    tenant_id,workspace_id,intelligence_run_id,created_at DESC
  );
ALTER TABLE operational_outcome_bindings ENABLE ROW LEVEL SECURITY;
ALTER TABLE operational_outcome_bindings FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS operational_outcome_bindings_scope
  ON operational_outcome_bindings;
CREATE POLICY operational_outcome_bindings_scope
  ON operational_outcome_bindings FOR SELECT TO omega_console
  USING (omega_rls_workspace_matches(tenant_id,workspace_id));
REVOKE ALL ON operational_outcome_bindings FROM PUBLIC;
REVOKE INSERT,UPDATE,DELETE,TRUNCATE ON operational_outcome_bindings
  FROM omega_console,omega_refinement,omega_mcp_infra,omega_airflow_dag;
GRANT SELECT ON operational_outcome_bindings TO omega_console;

CREATE OR REPLACE FUNCTION record_operational_outcome_binding(
  p_expected_run_id text,
  p_expected_run_ref text,
  p_intelligence_run_id bigint,
  p_bindings jsonb
) RETURNS text
LANGUAGE plpgsql SECURITY DEFINER
SET search_path=pg_catalog,public
AS $$
DECLARE
  tenant uuid := NULLIF(current_setting('app.tenant_id',true),'')::uuid;
  workspace uuid := NULLIF(current_setting('app.workspace_id',true),'')::uuid;
  intel intelligence_runs%ROWTYPE;
  binding jsonb;
  digest_value text;
BEGIN
  IF tenant IS NULL OR workspace IS NULL
     OR COALESCE(p_expected_run_id,'')=''
     OR COALESCE(p_expected_run_ref,'')=''
     OR jsonb_typeof(p_bindings)<>'array'
     OR jsonb_array_length(p_bindings)=0 THEN
    RAISE EXCEPTION 'operational outcome authority is incomplete'
      USING ERRCODE='42501';
  END IF;
  IF NOT EXISTS (
    SELECT 1 FROM pipeline_runs p
     WHERE p.tenant_id=tenant AND p.workspace_id=workspace
       AND p.run_id=p_expected_run_id AND p.status IN ('running','success')
  ) THEN
    RAISE EXCEPTION 'expected pipeline run is unavailable' USING ERRCODE='42501';
  END IF;
  SELECT * INTO intel FROM intelligence_runs i
   WHERE i.tenant_id=tenant AND i.workspace_id=workspace
     AND i.id=p_intelligence_run_id AND i.run_ref=p_expected_run_ref
     AND i.status='completed' AND i.signals_generated>0;
  IF NOT FOUND THEN
    RAISE EXCEPTION 'Intelligence outcome binding mismatch' USING ERRCODE='23514';
  END IF;
  IF (SELECT count(*) FROM jsonb_array_elements(p_bindings)) <>
     (SELECT count(DISTINCT item->>'dataset')
        FROM jsonb_array_elements(p_bindings) AS value(item)) THEN
    RAISE EXCEPTION 'duplicate publication binding' USING ERRCODE='23514';
  END IF;
  digest_value := encode(digest(convert_to(jsonb_build_object(
    'expected_run_id',p_expected_run_id,'run_ref',intel.run_ref,
    'intelligence_run_id',intel.id,'status',intel.status,
    'signals_generated',intel.signals_generated,'bindings',p_bindings
  )::text,'UTF8'),'sha256'),'hex');
  FOR binding IN SELECT value FROM jsonb_array_elements(p_bindings) LOOP
    IF jsonb_typeof(binding)<>'object'
       OR binding->>'dataset' !~ '^[A-Za-z_][A-Za-z0-9_]{0,127}$'
       OR binding->>'materialization_run_id' !~
          '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$'
       OR binding->>'receipt_id' !~
          '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$'
       OR binding->>'object_checksum' !~ '^[0-9a-f]{64}$'
       OR binding->>'evidence_digest' !~ '^[0-9a-f]{64}$'
       OR COALESCE((binding->>'head_generation')::bigint,0)<1 THEN
      RAISE EXCEPTION 'publication binding is incomplete' USING ERRCODE='23514';
    END IF;
    INSERT INTO operational_outcome_bindings (
      tenant_id,workspace_id,expected_run_id,expected_run_ref,
      intelligence_run_id,run_ref,dataset,materialization_run_id,receipt_id,
      head_generation,object_checksum,evidence_digest,outcome_digest
    ) VALUES (
      tenant,workspace,p_expected_run_id,p_expected_run_ref,intel.id,intel.run_ref,
      binding->>'dataset',(binding->>'materialization_run_id')::uuid,
      (binding->>'receipt_id')::uuid,(binding->>'head_generation')::bigint,
      binding->>'object_checksum',binding->>'evidence_digest',digest_value
    ) ON CONFLICT DO NOTHING;
  END LOOP;
  IF EXISTS (
    SELECT 1 FROM operational_outcome_bindings b
     WHERE b.tenant_id=tenant AND b.workspace_id=workspace
       AND b.expected_run_id=p_expected_run_id
       AND b.outcome_digest<>digest_value
  ) THEN
    RAISE EXCEPTION 'operational outcome replay mismatch' USING ERRCODE='23505';
  END IF;
  RETURN digest_value;
END $$;

ALTER FUNCTION record_operational_outcome_binding(text,text,bigint,jsonb)
  OWNER TO postgres;
REVOKE ALL ON FUNCTION record_operational_outcome_binding(text,text,bigint,jsonb)
  FROM PUBLIC;
GRANT EXECUTE ON FUNCTION record_operational_outcome_binding(text,text,bigint,jsonb)
  TO omega_console;
ALTER ROLE omega_console NOBYPASSRLS;

INSERT INTO schema_migrations(filename,applied_at)
VALUES ('99zzi_operational_outcome_binding.sql',clock_timestamp())
ON CONFLICT (filename) DO NOTHING;
COMMIT;
