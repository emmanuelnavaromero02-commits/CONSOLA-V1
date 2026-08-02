CREATE OR REPLACE FUNCTION omega_publication.gold_compatibility_relation(
    p_tenant uuid, p_workspace uuid, p_dataset text
) RETURNS text
LANGUAGE plpgsql IMMUTABLE STRICT
SET search_path = pg_catalog
AS $$
DECLARE
  scope_digest text;
BEGIN
  IF p_dataset !~ '^[A-Za-z_][A-Za-z0-9_]{0,127}$' THEN
    RAISE EXCEPTION 'invalid Gold dataset name' USING ERRCODE='22023';
  END IF;
  scope_digest := encode(public.digest(convert_to(
    p_tenant::text || ':' || p_workspace::text || ':' || p_dataset,
    'UTF8'),'sha256'),'hex');
  RETURN 'gold_' || substr(p_dataset,1,38) || '_' || substr(scope_digest,1,16);
END $$;

CREATE OR REPLACE FUNCTION omega_publication.publish_materialization(
    p_run uuid, p_expected_head uuid DEFAULT NULL
) RETURNS TABLE(receipt_id uuid, generation bigint, replayed boolean)
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, omega_publication
AS $$
DECLARE
  run_row omega_publication.materialization_runs%ROWTYPE;
  head_row omega_publication.dataset_publication_heads%ROWTYPE;
  receipt_row omega_publication.materialization_receipts%ROWTYPE;
  next_generation bigint;
  staged regclass;
  view_name text;
  legacy_name text;
  column_list text;
  column_record record;
  existing_type text;
  new_receipt uuid;
BEGIN
  SELECT * INTO run_row FROM omega_publication.materialization_runs
   WHERE materialization_run_id=p_run FOR UPDATE;
  IF FOUND THEN
    PERFORM omega_publication.assert_current_scope(
      run_row.tenant_id,run_row.workspace_id
    );
  END IF;
  IF NOT FOUND THEN
    RAISE EXCEPTION 'materialization run unavailable' USING ERRCODE='42501';
  END IF;
  SELECT * INTO receipt_row FROM omega_publication.materialization_receipts
   WHERE materialization_run_id=p_run;
  IF FOUND THEN
    RETURN QUERY SELECT receipt_row.receipt_id, receipt_row.generation, true;
    RETURN;
  END IF;
  IF run_row.status <> 'prepared' OR run_row.object_uri IS NULL
     OR run_row.object_version IS NULL
     OR run_row.object_checksum IS NULL OR run_row.schema_digest IS NULL
     OR run_row.evidence_digest IS NULL OR run_row.row_count IS NULL THEN
    RAISE EXCEPTION 'materialization is not publishable' USING ERRCODE='23514';
  END IF;
  IF p_expected_head IS DISTINCT FROM run_row.expected_head_run_id THEN
    RAISE EXCEPTION 'publication expected head mismatch' USING ERRCODE='40001';
  END IF;
  IF NOT EXISTS (
    SELECT 1
      FROM omega_publication.materialization_evidence e
      JOIN omega_publication.materialization_attestations a
        ON a.attestation_id=e.attestation_id
     WHERE e.materialization_run_id=run_row.materialization_run_id
       AND e.tenant_id=run_row.tenant_id AND e.workspace_id=run_row.workspace_id
       AND e.dataset=run_row.dataset AND e.layer=run_row.layer
       AND e.object_uri=run_row.object_uri
       AND e.object_version=run_row.object_version
       AND e.object_checksum=run_row.object_checksum
       AND e.row_count=run_row.row_count
       AND e.schema_digest=run_row.schema_digest
       AND e.evidence_digest=run_row.evidence_digest
       AND a.materialization_run_id=run_row.materialization_run_id
       AND a.tenant_id=run_row.tenant_id
       AND a.workspace_id=run_row.workspace_id
       AND a.dataset=run_row.dataset AND a.layer=run_row.layer
       AND a.attempt=run_row.attempt
       AND a.object_uri=e.object_uri
       AND a.object_version=e.object_version
       AND a.object_checksum=e.object_checksum
       AND a.row_count=e.row_count
       AND a.schema_digest=e.schema_digest
       AND a.input_digest=run_row.input_digest
       AND a.contract_digest=run_row.contract_digest
       AND a.lineage=e.lineage AND a.catalog=e.catalog
       AND a.verifier_identity='refinement.parquet-verifier/v1'
       AND omega_publication.verify_attestation(a.attestation_id)
       AND a.consumed_at IS NOT NULL
       AND a.invalidated_at IS NULL
       AND a.expires_at > clock_timestamp()
  ) THEN
    RAISE EXCEPTION 'materialization authority is incomplete or expired'
      USING ERRCODE='23514';
  END IF;
  PERFORM pg_advisory_xact_lock(hashtextextended(
    run_row.tenant_id::text || ':' || run_row.workspace_id::text || ':' ||
    run_row.dataset || ':' || run_row.layer, 0));
  SELECT * INTO head_row FROM omega_publication.dataset_publication_heads
   WHERE tenant_id=run_row.tenant_id AND workspace_id=run_row.workspace_id
     AND dataset=run_row.dataset AND layer=run_row.layer FOR UPDATE;
  IF head_row.materialization_run_id IS DISTINCT FROM run_row.expected_head_run_id THEN
    RAISE EXCEPTION 'publication head conflict' USING ERRCODE='40001';
  END IF;
  next_generation := COALESCE(head_row.generation, 0) + 1;

  IF run_row.layer = 'gold' THEN
    staged := to_regclass(format('omega_publication_stage.%I', run_row.staging_table));
    IF staged IS NULL THEN RAISE EXCEPTION 'prepared Gold stage missing'; END IF;
    IF run_row.gold_table IS DISTINCT FROM run_row.staging_table THEN
      RAISE EXCEPTION 'prepared Gold relation identity mismatch' USING ERRCODE='23514';
    END IF;
    INSERT INTO omega_publication.dataset_gold_relations (
      tenant_id, workspace_id, dataset, relation_name
    ) VALUES (
      run_row.tenant_id, run_row.workspace_id, run_row.dataset,
      omega_publication.gold_compatibility_relation(
        run_row.tenant_id,run_row.workspace_id,run_row.dataset
      )
    ) ON CONFLICT (tenant_id, workspace_id, dataset) DO UPDATE SET
      relation_name=EXCLUDED.relation_name;
    SELECT relation_name INTO legacy_name
      FROM omega_publication.dataset_gold_relations
     WHERE tenant_id=run_row.tenant_id AND workspace_id=run_row.workspace_id
       AND dataset=run_row.dataset;
    IF legacy_name IS NULL THEN
      RAISE EXCEPTION 'Gold relation mapping is unavailable' USING ERRCODE='23514';
    END IF;
    IF to_regclass(format('public.%I',legacy_name)) IS NULL THEN
      EXECUTE format(
        'CREATE TABLE public.%I (LIKE omega_publication_stage.%I INCLUDING DEFAULTS)',
        legacy_name,run_row.staging_table
      );
      EXECUTE format('ALTER TABLE public.%I OWNER TO omega_gold_owner',legacy_name);
    END IF;
    FOR column_record IN
      SELECT a.attname,format_type(a.atttypid,a.atttypmod) AS data_type
        FROM pg_attribute a
       WHERE a.attrelid=format('omega_publication_stage.%I',run_row.staging_table)::regclass
         AND a.attnum > 0 AND NOT a.attisdropped ORDER BY a.attnum
    LOOP
      SELECT format_type(a.atttypid,a.atttypmod) INTO existing_type
        FROM pg_attribute a
       WHERE a.attrelid=format('public.%I',legacy_name)::regclass
         AND a.attname=column_record.attname AND a.attnum > 0 AND NOT a.attisdropped;
      IF existing_type IS NULL THEN
        EXECUTE format('ALTER TABLE public.%I ADD COLUMN %I %s',
                       legacy_name,column_record.attname,column_record.data_type);
      ELSIF existing_type <> column_record.data_type THEN
        RAISE EXCEPTION 'legacy Gold column type conflict' USING ERRCODE='23514';
      END IF;
    END LOOP;
    EXECUTE format('ALTER TABLE public.%I ENABLE ROW LEVEL SECURITY',legacy_name);
    EXECUTE format('ALTER TABLE public.%I FORCE ROW LEVEL SECURITY',legacy_name);
    EXECUTE format('DROP POLICY IF EXISTS publication_owner ON public.%I',
                   legacy_name);
    EXECUTE format('CREATE POLICY publication_owner ON public.%I TO omega_gold_owner '
                   'USING (true) WITH CHECK (true)',
                   legacy_name);
    EXECUTE format('DROP POLICY IF EXISTS tenant_workspace_rls ON public.%I',
                   legacy_name);
    EXECUTE format(
      'CREATE POLICY tenant_workspace_rls ON public.%I FOR SELECT TO omega_refinement_gold USING ('
      'public.omega_gold_workspace_matches(tenant_id::text,workspace_id::text) AND '
      'EXISTS (SELECT 1 FROM omega_publication.dataset_publication_heads h '
      'WHERE h.tenant_id::text=NULLIF(current_setting(''app.tenant_id'',true),'''') '
      'AND h.workspace_id::text=NULLIF(current_setting(''app.workspace_id'',true),'''') '
      'AND h.dataset=%L AND h.layer=''gold''))',
      legacy_name,run_row.dataset
    );
    EXECUTE format('GRANT SELECT ON public.%I TO omega_refinement_gold',legacy_name);
    EXECUTE format('REVOKE INSERT,UPDATE,DELETE,TRUNCATE ON public.%I '
                   'FROM omega_refinement_gold,omega_gold_publisher',legacy_name);
    EXECUTE format(
      'ALTER TABLE omega_publication_stage.%I SET SCHEMA omega_publication_gold',
      run_row.staging_table
    );
    EXECUTE format(
      'DROP POLICY publication_scope ON omega_publication_gold.%I',
      run_row.gold_table
    );
    EXECUTE format(
      'CREATE POLICY published_head_scope ON omega_publication_gold.%I '
      'FOR SELECT TO omega_refinement_gold USING ('
      'tenant_id::text=NULLIF(current_setting(''app.tenant_id'',true),'''') AND '
      'workspace_id::text=NULLIF(current_setting(''app.workspace_id'',true),'''') AND '
      'EXISTS (SELECT 1 FROM omega_publication.dataset_publication_heads h '
      'WHERE h.tenant_id::text=NULLIF(current_setting(''app.tenant_id'',true),'''') '
      'AND h.workspace_id::text=NULLIF(current_setting(''app.workspace_id'',true),'''') '
      'AND h.materialization_run_id=%L::uuid))',
      run_row.gold_table, run_row.materialization_run_id
    );
    EXECUTE format(
      'CREATE POLICY publication_owner_scope ON omega_publication_gold.%I '
      'TO omega_gold_owner USING (true)',run_row.gold_table
    );
    EXECUTE format(
      'GRANT SELECT ON omega_publication_gold.%I TO omega_refinement_gold',
      run_row.gold_table
    );
    EXECUTE format(
      'REVOKE INSERT, UPDATE, DELETE, TRUNCATE ON omega_publication_gold.%I '
      'FROM omega_refinement_gold, omega_gold_publisher', run_row.gold_table
    );
    SELECT string_agg(format('%I',a.attname),',' ORDER BY a.attnum)
      INTO column_list FROM pg_attribute a
     WHERE a.attrelid=format('omega_publication_gold.%I',run_row.gold_table)::regclass
       AND a.attnum > 0 AND NOT a.attisdropped;
    EXECUTE format('DELETE FROM public.%I WHERE tenant_id::text=%L AND workspace_id::text=%L',
                   legacy_name,run_row.tenant_id::text,run_row.workspace_id::text);
    EXECUTE format('INSERT INTO public.%I (%s) SELECT %s FROM omega_publication_gold.%I',
                   legacy_name,column_list,column_list,run_row.gold_table);
    view_name := 'v_' || md5(run_row.tenant_id::text || ':' ||
                             run_row.workspace_id::text || ':' || run_row.dataset);
    EXECUTE format('DROP VIEW IF EXISTS omega_publication_views.%I', view_name);
    EXECUTE format(
      'CREATE VIEW omega_publication_views.%I WITH (security_invoker=true) AS '
      'SELECT * FROM omega_publication_gold.%I', view_name, run_row.gold_table
    );
    EXECUTE format('ALTER VIEW omega_publication_views.%I OWNER TO omega_gold_owner', view_name);
    EXECUTE format('GRANT SELECT ON omega_publication_views.%I TO omega_refinement_gold', view_name);
  END IF;

  new_receipt := gen_random_uuid();
  INSERT INTO omega_publication.materialization_receipts (
    receipt_id, materialization_run_id, tenant_id, workspace_id, dataset,
    layer,input_digest,contract_digest,object_checksum,object_version,schema_digest,
    evidence_digest, row_count, generation
  ) VALUES (new_receipt, p_run, run_row.tenant_id, run_row.workspace_id,
            run_row.dataset, run_row.layer, run_row.input_digest,
            run_row.contract_digest,run_row.object_checksum,run_row.object_version,
            run_row.schema_digest,
            run_row.evidence_digest, run_row.row_count, next_generation);
  INSERT INTO omega_publication.dataset_publication_heads (
    tenant_id, workspace_id, dataset, layer, materialization_run_id, generation
  ) VALUES (run_row.tenant_id, run_row.workspace_id, run_row.dataset, run_row.layer,
            p_run, next_generation)
  ON CONFLICT (tenant_id, workspace_id, dataset, layer) DO UPDATE SET
    materialization_run_id=EXCLUDED.materialization_run_id,
    generation=EXCLUDED.generation, published_at=clock_timestamp();
  UPDATE omega_publication.materialization_runs
     SET status='published', published_at=clock_timestamp()
   WHERE materialization_run_id=p_run;
  RETURN QUERY SELECT new_receipt, next_generation, false;
END $$;

ALTER FUNCTION omega_publication.publish_materialization(uuid,uuid)
  OWNER TO omega_gold_owner;
ALTER FUNCTION omega_publication.gold_compatibility_relation(uuid,uuid,text)
  OWNER TO omega_gold_owner;
REVOKE ALL ON ALL FUNCTIONS IN SCHEMA omega_publication
  FROM PUBLIC, omega_refinement_gold;
GRANT EXECUTE ON FUNCTION omega_publication.publish_materialization(uuid,uuid)
  TO omega_gold_publisher;
GRANT EXECUTE ON FUNCTION omega_publication.gold_compatibility_relation(uuid,uuid,text)
  TO omega_gold_publisher;

CREATE OR REPLACE VIEW omega_publication.published_lineage
WITH (security_invoker=true) AS
SELECT h.tenant_id,h.workspace_id,h.dataset,h.layer,h.generation,
       h.materialization_run_id,rec.receipt_id,e.object_uri,e.object_version,
       e.object_checksum,
       e.schema_digest,e.evidence_digest,e.row_count,e.lineage,e.created_at
  FROM omega_publication.dataset_publication_heads h
  JOIN omega_publication.materialization_receipts rec
    ON rec.materialization_run_id=h.materialization_run_id
   AND rec.tenant_id=h.tenant_id AND rec.workspace_id=h.workspace_id
   AND rec.dataset=h.dataset AND rec.layer=h.layer
   AND rec.generation=h.generation
  JOIN omega_publication.materialization_evidence e
    ON e.materialization_run_id=h.materialization_run_id
   AND e.tenant_id=h.tenant_id AND e.workspace_id=h.workspace_id
   AND e.dataset=h.dataset AND e.layer=h.layer
   AND e.object_checksum=rec.object_checksum
   AND e.object_version=rec.object_version
   AND e.schema_digest=rec.schema_digest
   AND e.evidence_digest=rec.evidence_digest
   AND e.row_count=rec.row_count;
ALTER VIEW omega_publication.published_lineage OWNER TO omega_gold_owner;
GRANT SELECT ON omega_publication.published_lineage TO omega_refinement_gold;

INSERT INTO schema_migrations(filename,applied_at)
VALUES ('gold/42_staged_publication_cas.sql',NOW())
ON CONFLICT (filename) DO NOTHING;
