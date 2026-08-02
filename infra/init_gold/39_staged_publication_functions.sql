CREATE OR REPLACE FUNCTION omega_publication.reserve_materialization(
    p_run uuid, p_tenant uuid, p_workspace uuid, p_dataset text, p_layer text,
    p_input_digest text, p_contract_digest text, p_expected_head uuid
) RETURNS TABLE(materialization_run_id uuid, status text)
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, omega_publication
AS $$
DECLARE
  existing omega_publication.materialization_runs%ROWTYPE;
  expected_head omega_publication.dataset_publication_heads%ROWTYPE;
BEGIN
  IF p_dataset !~ '^[A-Za-z_][A-Za-z0-9_]{0,127}$' OR p_layer NOT IN ('silver', 'gold')
     OR p_input_digest !~ '^[0-9a-f]{64}$' OR p_contract_digest !~ '^[0-9a-f]{64}$' THEN
    RAISE EXCEPTION 'invalid materialization identity' USING ERRCODE = '22023';
  END IF;
  IF p_tenant::text <> NULLIF(current_setting('app.tenant_id', true), '')
     OR p_workspace::text <> NULLIF(current_setting('app.workspace_id', true), '') THEN
    RAISE EXCEPTION 'materialization scope mismatch' USING ERRCODE = '42501';
  END IF;
  SELECT r.* INTO existing FROM omega_publication.materialization_runs r
   WHERE r.materialization_run_id = p_run;
  IF FOUND THEN
    IF (existing.tenant_id, existing.workspace_id, existing.dataset, existing.layer,
        existing.input_digest, existing.contract_digest)
       IS DISTINCT FROM
       (p_tenant, p_workspace, p_dataset, p_layer, p_input_digest, p_contract_digest) THEN
      RAISE EXCEPTION 'materialization run identity mismatch' USING ERRCODE = '23505';
    END IF;
    IF existing.status IN ('failed', 'recoverable_failed') THEN
      SELECT * INTO expected_head FROM omega_publication.dataset_publication_heads h
       WHERE h.tenant_id=p_tenant AND h.workspace_id=p_workspace
         AND h.dataset=p_dataset AND h.layer=p_layer;
      IF expected_head.materialization_run_id IS DISTINCT FROM p_expected_head THEN
        RAISE EXCEPTION 'materialization retry head conflict' USING ERRCODE='40001';
      END IF;
      UPDATE omega_publication.materialization_attestations AS retry_attestation
         SET invalidated_at=COALESCE(
           retry_attestation.invalidated_at,clock_timestamp()
         )
       WHERE retry_attestation.materialization_run_id=p_run;
      UPDATE omega_publication.materialization_runs AS retry_run
         SET status='reserved', prepared_at=NULL, staging_table=NULL,
             object_uri=NULL, object_checksum=NULL, row_count=NULL,
             schema_digest=NULL, evidence_digest=NULL, gold_table=NULL,
             recovery_reason=NULL, attempt=retry_run.attempt+1
       WHERE retry_run.materialization_run_id=p_run;
      RETURN QUERY SELECT existing.materialization_run_id,'reserved'::text;
      RETURN;
    END IF;
    RETURN QUERY SELECT existing.materialization_run_id, existing.status;
    RETURN;
  END IF;
  SELECT * INTO expected_head FROM omega_publication.dataset_publication_heads h
   WHERE h.tenant_id=p_tenant AND h.workspace_id=p_workspace
     AND h.dataset=p_dataset AND h.layer=p_layer;
  IF expected_head.materialization_run_id IS DISTINCT FROM p_expected_head THEN
    RAISE EXCEPTION 'materialization reservation head conflict' USING ERRCODE='40001';
  END IF;
  INSERT INTO omega_publication.materialization_runs (
    materialization_run_id, tenant_id, workspace_id, dataset, layer,
    input_digest, contract_digest, expected_head_run_id,
    expected_head_generation
  ) VALUES (
    p_run, p_tenant, p_workspace, p_dataset, p_layer, p_input_digest,
    p_contract_digest, p_expected_head,
    expected_head.generation
  );
  RETURN QUERY SELECT p_run, 'reserved'::text;
END $$;

CREATE OR REPLACE FUNCTION omega_publication.record_attestation(
    p_run uuid, p_object_uri text, p_object_checksum text, p_row_count bigint,
    p_lineage jsonb, p_catalog jsonb
) RETURNS text
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, omega_publication
AS $$
DECLARE
  run_row omega_publication.materialization_runs%ROWTYPE;
  derived_digest text;
  derived_schema_digest text;
BEGIN
  SELECT * INTO run_row FROM omega_publication.materialization_runs
   WHERE materialization_run_id=p_run FOR UPDATE;
  IF NOT FOUND OR run_row.status <> 'reserved' THEN
    RAISE EXCEPTION 'materialization run is not attestable' USING ERRCODE='23514';
  END IF;
  IF p_object_uri !~ '^(s3|gs)://' OR p_object_checksum !~ '^[0-9a-f]{64}$'
     OR p_row_count < 0 OR jsonb_typeof(p_lineage) <> 'object'
     OR jsonb_typeof(p_catalog) <> 'array'
     OR jsonb_array_length(p_catalog)=0
     OR p_lineage->>'input_digest' IS DISTINCT FROM run_row.input_digest
     OR p_lineage->>'contract_digest' IS DISTINCT FROM run_row.contract_digest
     OR EXISTS (
       SELECT 1 FROM jsonb_array_elements(p_catalog) item
        WHERE jsonb_typeof(item) <> 'object'
           OR COALESCE(item->>'name','') !~ '^[A-Za-z_][A-Za-z0-9_]{0,127}$'
           OR COALESCE(item->>'type','') = ''
     ) THEN
    RAISE EXCEPTION 'materialization attestation is incomplete' USING ERRCODE='23514';
  END IF;
  derived_schema_digest := encode(
    public.digest(convert_to(p_catalog::text,'UTF8'),'sha256'),'hex'
  );
  derived_digest := encode(public.digest(convert_to(jsonb_build_object(
    'run',p_run,'tenant_id',run_row.tenant_id,'workspace_id',run_row.workspace_id,
    'dataset',run_row.dataset,'layer',run_row.layer,'attempt',run_row.attempt,
    'object_uri',p_object_uri,'object_checksum',p_object_checksum,
    'row_count',p_row_count,'schema_digest',derived_schema_digest,
    'input_digest',run_row.input_digest,'contract_digest',run_row.contract_digest,
    'verifier_identity','refinement.parquet-verifier/v1',
    'lineage',p_lineage,'catalog',p_catalog
  )::text,'UTF8'),'sha256'),'hex');
  INSERT INTO omega_publication.materialization_attestations (
    materialization_run_id,tenant_id,workspace_id,dataset,layer,attempt,
    object_uri,object_checksum,row_count,schema_digest,input_digest,
    contract_digest,lineage,catalog,verifier_identity,attestation_digest,expires_at
  ) VALUES (
    p_run,run_row.tenant_id,run_row.workspace_id,run_row.dataset,run_row.layer,
    run_row.attempt,p_object_uri,p_object_checksum,p_row_count,
    derived_schema_digest,run_row.input_digest,run_row.contract_digest,
    p_lineage,p_catalog,'refinement.parquet-verifier/v1',derived_digest,
    clock_timestamp()+interval '15 minutes'
  ) ON CONFLICT (materialization_run_id,attestation_digest) DO NOTHING;
  RETURN derived_digest;
END $$;

CREATE OR REPLACE FUNCTION omega_publication.create_gold_stage(
    p_run uuid, p_columns jsonb
) RETURNS text
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, omega_publication
AS $$
DECLARE
  run_row omega_publication.materialization_runs%ROWTYPE;
  item jsonb;
  column_name text;
  column_type text;
  definitions text := '';
  stage_name text := 'run_' || replace(p_run::text, '-', '');
BEGIN
  SELECT * INTO run_row FROM omega_publication.materialization_runs
   WHERE materialization_run_id=p_run FOR UPDATE;
  IF NOT FOUND OR run_row.layer <> 'gold' OR run_row.status <> 'reserved' THEN
    RAISE EXCEPTION 'Gold run is not reserved';
  END IF;
  IF jsonb_typeof(p_columns) <> 'array' OR jsonb_array_length(p_columns) = 0 THEN
    RAISE EXCEPTION 'Gold stage schema is empty';
  END IF;
  FOR item IN SELECT value FROM jsonb_array_elements(p_columns) LOOP
    column_name := item->>'name';
    column_type := upper(item->>'type');
    IF column_name !~ '^[A-Za-z_][A-Za-z0-9_]{0,127}$'
       OR column_type !~ '^(TEXT|BOOLEAN|SMALLINT|INTEGER|BIGINT|REAL|DOUBLE PRECISION|DATE|TIMESTAMP|TIMESTAMPTZ|TIME|JSONB|DECIMAL\([0-9]{1,2},[0-9]{1,2}\))$' THEN
      RAISE EXCEPTION 'invalid Gold stage schema' USING ERRCODE='22023';
    END IF;
    definitions := definitions || CASE WHEN definitions = '' THEN '' ELSE ', ' END
      || format('%I %s%s', column_name, column_type,
                CASE WHEN column_name IN ('tenant_id','workspace_id') THEN ' NOT NULL' ELSE '' END);
  END LOOP;
  IF NOT EXISTS (SELECT 1 FROM jsonb_array_elements(p_columns) v WHERE v->>'name'='tenant_id')
     OR NOT EXISTS (SELECT 1 FROM jsonb_array_elements(p_columns) v WHERE v->>'name'='workspace_id') THEN
    RAISE EXCEPTION 'Gold stage must be tenant/workspace scoped';
  END IF;
  IF to_regclass(format('omega_publication_stage.%I', stage_name)) IS NULL THEN
    EXECUTE format('CREATE TABLE omega_publication_stage.%I (%s)', stage_name, definitions);
    EXECUTE format('ALTER TABLE omega_publication_stage.%I OWNER TO omega_gold_owner', stage_name);
  ELSE
    EXECUTE format('TRUNCATE TABLE omega_publication_stage.%I', stage_name);
  END IF;
  EXECUTE format('ALTER TABLE omega_publication_stage.%I ENABLE ROW LEVEL SECURITY', stage_name);
  EXECUTE format('ALTER TABLE omega_publication_stage.%I FORCE ROW LEVEL SECURITY', stage_name);
  EXECUTE format('DROP POLICY IF EXISTS publication_scope ON omega_publication_stage.%I', stage_name);
  EXECUTE format(
    'CREATE POLICY publication_scope ON omega_publication_stage.%I '
    'TO omega_gold_owner, omega_gold_publisher, omega_refinement_gold '
    'USING (tenant_id::text = %L AND workspace_id::text = %L '
    'AND tenant_id::text = NULLIF(current_setting(''app.tenant_id'', true), '''') '
    'AND workspace_id::text = NULLIF(current_setting(''app.workspace_id'', true), '''')) '
    'WITH CHECK (tenant_id::text = %L AND workspace_id::text = %L '
    'AND tenant_id::text = NULLIF(current_setting(''app.tenant_id'', true), '''') '
    'AND workspace_id::text = NULLIF(current_setting(''app.workspace_id'', true), ''''))',
    stage_name, run_row.tenant_id::text, run_row.workspace_id::text,
    run_row.tenant_id::text, run_row.workspace_id::text
  );
  EXECUTE format('GRANT INSERT, SELECT ON omega_publication_stage.%I TO omega_gold_publisher', stage_name);
  UPDATE omega_publication.materialization_runs
     SET staging_table=stage_name, gold_table=stage_name
   WHERE materialization_run_id=p_run;
  RETURN stage_name;
END $$;

DROP FUNCTION IF EXISTS omega_publication.mark_prepared(
  uuid,text,text,bigint,text,text,text,text,jsonb,jsonb
);
CREATE OR REPLACE FUNCTION omega_publication.mark_prepared(
    p_run uuid, p_object_uri text, p_object_checksum text, p_row_count bigint,
    p_gold_table text, p_staging_table text, p_lineage jsonb, p_catalog jsonb
) RETURNS TABLE(schema_digest text, evidence_digest text)
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, omega_publication
AS $$
DECLARE
  current_run omega_publication.materialization_runs%ROWTYPE;
  staged_count bigint;
  staged_scope_violations bigint;
  staged_catalog jsonb;
  authoritative_catalog jsonb;
  attestation_row omega_publication.materialization_attestations%ROWTYPE;
  derived_schema_digest text;
  derived_evidence_digest text;
BEGIN
  SELECT * INTO current_run FROM omega_publication.materialization_runs
   WHERE materialization_run_id = p_run FOR UPDATE;
  IF NOT FOUND THEN RAISE EXCEPTION 'materialization run not reserved'; END IF;
  IF current_run.status = 'published' THEN RETURN; END IF;
  IF p_object_uri IS NULL OR p_object_uri !~ '^(s3|gs)://'
     OR p_object_checksum !~ '^[0-9a-f]{64}$' OR p_row_count < 0
     OR jsonb_typeof(p_lineage) <> 'object' OR jsonb_typeof(p_catalog) <> 'array'
     OR jsonb_array_length(p_catalog)=0
     OR position('/tenant_id=' || current_run.tenant_id::text ||
                 '/workspace_id=' || current_run.workspace_id::text || '/' IN p_object_uri) = 0
     OR position('/_pending/' || replace(p_run::text, '-', '') || '/' IN p_object_uri) = 0
     OR right(p_object_uri, 64 + length('.parquet')) <> p_object_checksum || '.parquet'
     OR (current_run.layer = 'gold' AND (p_gold_table !~ '^run_[0-9a-f]{32}$'
         OR p_staging_table IS DISTINCT FROM p_gold_table)) THEN
    RAISE EXCEPTION 'incomplete publication evidence' USING ERRCODE = '23514';
  END IF;
  IF NOT (p_lineage ?& ARRAY[
       'source_entity','source_load_date','source_batch_id',
       'sql_digest','column_mapping_digest'
     ])
     OR p_lineage->>'sql_digest' !~ '^[0-9a-f]{64}$'
     OR p_lineage->>'column_mapping_digest' !~ '^[0-9a-f]{64}$'
     OR p_lineage->>'input_digest' IS DISTINCT FROM current_run.input_digest
     OR p_lineage->>'contract_digest' IS DISTINCT FROM current_run.contract_digest
     OR EXISTS (
       SELECT 1 FROM jsonb_array_elements(p_catalog) item
        WHERE jsonb_typeof(item) <> 'object'
           OR COALESCE(item->>'name','') !~ '^[A-Za-z_][A-Za-z0-9_]{0,127}$'
           OR COALESCE(item->>'type','') = ''
     ) THEN
    RAISE EXCEPTION 'publication lineage or catalog is incomplete' USING ERRCODE='23514';
  END IF;
  IF current_run.layer = 'gold' THEN
    IF current_run.staging_table IS DISTINCT FROM p_staging_table
       OR current_run.gold_table IS DISTINCT FROM p_gold_table THEN
      RAISE EXCEPTION 'Gold stage identity mismatch';
    END IF;
    EXECUTE format(
      'SELECT count(*), count(*) FILTER (WHERE tenant_id IS NULL OR workspace_id IS NULL '
      'OR tenant_id::text IS DISTINCT FROM $1 OR workspace_id::text IS DISTINCT FROM $2) '
      'FROM omega_publication_stage.%I', p_staging_table
    ) INTO staged_count, staged_scope_violations
      USING current_run.tenant_id::text, current_run.workspace_id::text;
    IF staged_count <> p_row_count OR staged_scope_violations <> 0 THEN
      RAISE EXCEPTION 'Gold stage row count or scope mismatch' USING ERRCODE='23514';
    END IF;
    SELECT jsonb_agg(
             jsonb_build_object('name',a.attname,'type',format_type(a.atttypid,a.atttypmod))
             ORDER BY a.attnum
           ) INTO staged_catalog
      FROM pg_attribute a
     WHERE a.attrelid=format('omega_publication_stage.%I',p_staging_table)::regclass
       AND a.attnum > 0 AND NOT a.attisdropped;
    IF staged_catalog IS NULL OR jsonb_array_length(staged_catalog)=0 THEN
      RAISE EXCEPTION 'Gold stage catalog is empty' USING ERRCODE='23514';
    END IF;
    IF (SELECT jsonb_agg(item->>'name' ORDER BY ordinal)
          FROM jsonb_array_elements(p_catalog) WITH ORDINALITY AS value(item,ordinal))
       IS DISTINCT FROM
       (SELECT jsonb_agg(item->>'name' ORDER BY ordinal)
          FROM jsonb_array_elements(staged_catalog)
               WITH ORDINALITY AS value(item,ordinal)) THEN
      RAISE EXCEPTION 'Gold catalog does not match prepared relation' USING ERRCODE='23514';
    END IF;
    EXECUTE format(
      'REVOKE INSERT, UPDATE, DELETE, TRUNCATE ON omega_publication_stage.%I FROM omega_gold_publisher',
      p_staging_table
    );
  END IF;
  authoritative_catalog := p_catalog;
  derived_schema_digest := encode(
    public.digest(convert_to(authoritative_catalog::text,'UTF8'),'sha256'),'hex'
  );
  SELECT a.* INTO attestation_row
    FROM omega_publication.materialization_attestations a
     WHERE a.materialization_run_id=p_run
       AND a.tenant_id=current_run.tenant_id
       AND a.workspace_id=current_run.workspace_id
       AND a.dataset=current_run.dataset AND a.layer=current_run.layer
       AND a.attempt=current_run.attempt
       AND a.object_uri=p_object_uri
       AND a.object_checksum=p_object_checksum
       AND a.row_count=p_row_count
       AND a.schema_digest=derived_schema_digest
       AND a.input_digest=current_run.input_digest
       AND a.contract_digest=current_run.contract_digest
       AND a.lineage=p_lineage
       AND a.catalog=authoritative_catalog
       AND a.verifier_identity='refinement.parquet-verifier/v1'
       AND a.consumed_at IS NULL
       AND a.invalidated_at IS NULL
       AND a.expires_at > clock_timestamp()
     ORDER BY a.created_at DESC LIMIT 1 FOR UPDATE;
  IF NOT FOUND THEN
    RAISE EXCEPTION 'server-owned materialization attestation is required'
      USING ERRCODE='42501';
  END IF;
  UPDATE omega_publication.materialization_attestations
     SET consumed_at=clock_timestamp()
   WHERE attestation_id=attestation_row.attestation_id;
  derived_evidence_digest := encode(public.digest(convert_to(jsonb_build_object(
    'run',p_run,'tenant_id',current_run.tenant_id,'workspace_id',current_run.workspace_id,
    'dataset',current_run.dataset,'layer',current_run.layer,
    'input_digest',current_run.input_digest,'contract_digest',current_run.contract_digest,
    'object_uri',p_object_uri,'object_checksum',p_object_checksum,
    'row_count',p_row_count,'schema_digest',derived_schema_digest,
    'lineage',p_lineage,'catalog',authoritative_catalog
  )::text,'UTF8'),'sha256'),'hex');
  INSERT INTO omega_publication.materialization_evidence (
    materialization_run_id, tenant_id, workspace_id, dataset, layer,
    object_uri, object_checksum, row_count, schema_digest, evidence_digest,
    attestation_id,
    lineage, catalog
  ) VALUES (
    p_run, current_run.tenant_id, current_run.workspace_id,
    current_run.dataset, current_run.layer, p_object_uri, p_object_checksum,
    p_row_count, derived_schema_digest, derived_evidence_digest,
    attestation_row.attestation_id,
    p_lineage, authoritative_catalog
  ) ON CONFLICT (materialization_run_id) DO NOTHING;
  IF NOT EXISTS (
    SELECT 1 FROM omega_publication.materialization_evidence e
     WHERE e.materialization_run_id=p_run
       AND (e.tenant_id, e.workspace_id, e.dataset, e.layer, e.object_uri,
            e.object_checksum, e.row_count, e.schema_digest, e.evidence_digest,
            e.attestation_id,
            e.lineage, e.catalog)
           IS NOT DISTINCT FROM
           (current_run.tenant_id, current_run.workspace_id, current_run.dataset,
            current_run.layer, p_object_uri, p_object_checksum, p_row_count,
            derived_schema_digest, derived_evidence_digest,
            attestation_row.attestation_id,
            p_lineage, authoritative_catalog)
  ) THEN
    RAISE EXCEPTION 'materialization evidence replay mismatch' USING ERRCODE='23505';
  END IF;
  UPDATE omega_publication.materialization_runs SET
    object_uri=p_object_uri, object_checksum=p_object_checksum, row_count=p_row_count,
    schema_digest=derived_schema_digest, evidence_digest=derived_evidence_digest,
    gold_table=p_gold_table, staging_table=p_staging_table,
    status='prepared', prepared_at=clock_timestamp()
  WHERE materialization_run_id=p_run;
  RETURN QUERY SELECT derived_schema_digest, derived_evidence_digest;
END $$;

CREATE OR REPLACE FUNCTION omega_publication.quarantine_prepared(
    p_run uuid, p_reason text
) RETURNS void
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, omega_publication
AS $$
DECLARE
  run_row omega_publication.materialization_runs%ROWTYPE;
  evidence_row omega_publication.materialization_evidence%ROWTYPE;
BEGIN
  SELECT * INTO run_row FROM omega_publication.materialization_runs
   WHERE materialization_run_id=p_run FOR UPDATE;
  IF NOT FOUND OR run_row.status <> 'prepared' THEN
    RAISE EXCEPTION 'prepared materialization is not recoverable';
  END IF;
  IF EXISTS (SELECT 1 FROM omega_publication.dataset_publication_heads
              WHERE materialization_run_id=p_run)
     OR EXISTS (SELECT 1 FROM omega_publication.materialization_receipts
                WHERE materialization_run_id=p_run) THEN
    RAISE EXCEPTION 'published materialization cannot be quarantined';
  END IF;
  SELECT * INTO evidence_row FROM omega_publication.materialization_evidence
   WHERE materialization_run_id=p_run FOR UPDATE;
  INSERT INTO omega_publication.materialization_recovery_events (
    materialization_run_id,tenant_id,workspace_id,attempt,reason,evidence
  ) VALUES (
    p_run,run_row.tenant_id,run_row.workspace_id,run_row.attempt,
    left(COALESCE(NULLIF(p_reason,''),'prepared_object_unavailable'),240),
    to_jsonb(evidence_row)
  );
  DELETE FROM omega_publication.materialization_evidence
   WHERE materialization_run_id=p_run;
  UPDATE omega_publication.materialization_attestations
     SET invalidated_at=COALESCE(invalidated_at,clock_timestamp())
   WHERE materialization_run_id=p_run;
  IF run_row.staging_table IS NOT NULL THEN
    EXECUTE format('DROP TABLE IF EXISTS omega_publication_stage.%I',run_row.staging_table);
  END IF;
  UPDATE omega_publication.materialization_runs
     SET status='recoverable_failed',recovery_reason=left(p_reason,240),
         staging_table=NULL,gold_table=NULL
   WHERE materialization_run_id=p_run;
END $$;

CREATE OR REPLACE FUNCTION omega_publication.abandon_materialization(p_run uuid)
RETURNS void
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, omega_publication
AS $$
DECLARE
  run_row omega_publication.materialization_runs%ROWTYPE;
BEGIN
  SELECT * INTO run_row FROM omega_publication.materialization_runs
   WHERE materialization_run_id=p_run FOR UPDATE;
  IF NOT FOUND OR run_row.status IN ('published','legacy_unverified') THEN RETURN; END IF;
  IF EXISTS (
    SELECT 1 FROM omega_publication.dataset_publication_heads
     WHERE materialization_run_id=p_run
  ) THEN RAISE EXCEPTION 'published run cannot be abandoned'; END IF;
  IF run_row.staging_table IS NOT NULL THEN
    EXECUTE format('DROP TABLE IF EXISTS omega_publication_stage.%I', run_row.staging_table);
  END IF;
  UPDATE omega_publication.materialization_attestations
     SET invalidated_at=COALESCE(invalidated_at,clock_timestamp())
   WHERE materialization_run_id=p_run;
  UPDATE omega_publication.materialization_runs
     SET status='failed', staging_table=NULL
   WHERE materialization_run_id=p_run;
END $$;

ALTER FUNCTION omega_publication.reserve_materialization(uuid,uuid,uuid,text,text,text,text,uuid) OWNER TO omega_gold_owner;
ALTER FUNCTION omega_publication.create_gold_stage(uuid,jsonb) OWNER TO omega_gold_owner;
ALTER FUNCTION omega_publication.record_attestation(uuid,text,text,bigint,jsonb,jsonb) OWNER TO omega_gold_owner;
ALTER FUNCTION omega_publication.mark_prepared(uuid,text,text,bigint,text,text,jsonb,jsonb) OWNER TO omega_gold_owner;
ALTER FUNCTION omega_publication.abandon_materialization(uuid) OWNER TO omega_gold_owner;
ALTER FUNCTION omega_publication.quarantine_prepared(uuid,text) OWNER TO omega_gold_owner;
REVOKE ALL ON ALL FUNCTIONS IN SCHEMA omega_publication FROM PUBLIC, omega_refinement_gold;
GRANT EXECUTE ON FUNCTION omega_publication.reserve_materialization(uuid,uuid,uuid,text,text,text,text,uuid) TO omega_gold_publisher;
GRANT EXECUTE ON FUNCTION omega_publication.create_gold_stage(uuid,jsonb) TO omega_gold_publisher;
GRANT EXECUTE ON FUNCTION omega_publication.record_attestation(uuid,text,text,bigint,jsonb,jsonb) TO omega_refinement_gold;
GRANT EXECUTE ON FUNCTION omega_publication.mark_prepared(uuid,text,text,bigint,text,text,jsonb,jsonb) TO omega_gold_publisher;
GRANT EXECUTE ON FUNCTION omega_publication.abandon_materialization(uuid) TO omega_gold_publisher;
GRANT EXECUTE ON FUNCTION omega_publication.quarantine_prepared(uuid,text) TO omega_gold_publisher;
