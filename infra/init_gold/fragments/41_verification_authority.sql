CREATE OR REPLACE FUNCTION omega_publication.submit_verification_candidate(
    p_run uuid,p_object_uri text,p_object_version text,p_object_checksum text,
    p_row_count bigint,p_lineage jsonb,p_catalog jsonb
) RETURNS uuid LANGUAGE plpgsql SECURITY DEFINER
SET search_path=pg_catalog,omega_publication AS $$
DECLARE
  run_row omega_publication.materialization_runs%ROWTYPE;
  digest_value text;
  candidate uuid;
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
  IF run_row.status<>'reserved' OR COALESCE(p_object_version,'')=''
     OR p_object_uri !~ '^(s3|gs)://' OR p_object_checksum !~ '^[0-9a-f]{64}$'
     OR p_row_count<0 OR jsonb_typeof(p_lineage)<>'object'
     OR jsonb_typeof(p_catalog)<>'array' OR jsonb_array_length(p_catalog)=0
     OR p_lineage->>'input_digest' IS DISTINCT FROM run_row.input_digest
     OR p_lineage->>'contract_digest' IS DISTINCT FROM run_row.contract_digest THEN
    RAISE EXCEPTION 'verification candidate is incomplete' USING ERRCODE='23514';
  END IF;
  digest_value := encode(public.digest(convert_to(jsonb_build_object(
    'run',p_run,'attempt',run_row.attempt,'tenant_id',run_row.tenant_id,
    'workspace_id',run_row.workspace_id,'dataset',run_row.dataset,
    'layer',run_row.layer,'object_uri',p_object_uri,
    'object_version',p_object_version,'object_checksum',p_object_checksum,
    'row_count',p_row_count,'input_digest',run_row.input_digest,
    'contract_digest',run_row.contract_digest,'lineage',p_lineage,
    'catalog',p_catalog)::text,'UTF8'),'sha256'),'hex');
  SELECT c.candidate_id INTO candidate
    FROM omega_publication.materialization_verification_candidates c
   WHERE c.materialization_run_id=p_run AND c.attempt=run_row.attempt
     AND c.candidate_digest=digest_value AND c.status='pending'
     AND c.expires_at>clock_timestamp() FOR UPDATE;
  IF FOUND THEN RETURN candidate; END IF;
  UPDATE omega_publication.materialization_verification_candidates
     SET status='expired'
   WHERE materialization_run_id=p_run AND attempt=run_row.attempt
     AND status='pending' AND expires_at<=clock_timestamp();
  INSERT INTO omega_publication.materialization_verification_candidates(
    materialization_run_id,tenant_id,workspace_id,dataset,layer,attempt,
    object_uri,object_version,object_checksum,row_count,schema_digest,
    input_digest,contract_digest,lineage,catalog,candidate_digest,expires_at
  ) VALUES (p_run,run_row.tenant_id,run_row.workspace_id,run_row.dataset,
    run_row.layer,run_row.attempt,p_object_uri,p_object_version,p_object_checksum,
    p_row_count,encode(public.digest(convert_to(p_catalog::text,'UTF8'),'sha256'),'hex'),
    run_row.input_digest,run_row.contract_digest,p_lineage,p_catalog,digest_value,
    clock_timestamp()+interval '15 minutes')
  RETURNING candidate_id INTO candidate;
  RETURN candidate;
END $$;

CREATE OR REPLACE FUNCTION omega_publication.load_verification_candidate(
  p_candidate uuid
) RETURNS TABLE(
  candidate_id uuid,materialization_run_id uuid,tenant_id uuid,workspace_id uuid,
  dataset text,layer text,object_uri text,object_version text,
  object_checksum text,row_count bigint,lineage jsonb,catalog jsonb
) LANGUAGE plpgsql STABLE SECURITY DEFINER
SET search_path=pg_catalog,omega_publication AS $$
BEGIN
  IF session_user<>'omega_gold_verifier' THEN
    RAISE EXCEPTION 'verifier identity required' USING ERRCODE='42501';
  END IF;
  RETURN QUERY
  SELECT c.candidate_id,c.materialization_run_id,c.tenant_id,c.workspace_id,
         c.dataset,c.layer,c.object_uri,c.object_version,c.object_checksum,
         c.row_count,c.lineage,c.catalog
    FROM omega_publication.materialization_verification_candidates c
   WHERE c.candidate_id=p_candidate AND c.status='pending'
     AND c.expires_at>clock_timestamp();
END $$;

CREATE OR REPLACE FUNCTION omega_publication.record_attestation(p_candidate uuid)
RETURNS text LANGUAGE plpgsql SECURITY DEFINER
SET search_path=pg_catalog,omega_publication AS $$
DECLARE
  candidate omega_publication.materialization_verification_candidates%ROWTYPE;
  envelope jsonb;
  digest_value text;
  signature_value text;
  issued timestamptz;
  selected_key text := 'v1';
BEGIN
  IF session_user<>'omega_gold_verifier' THEN
    RAISE EXCEPTION 'verifier identity required' USING ERRCODE='42501';
  END IF;
  SELECT * INTO candidate
    FROM omega_publication.materialization_verification_candidates c
   WHERE c.candidate_id=p_candidate AND c.status='pending'
     AND c.expires_at>clock_timestamp() FOR UPDATE;
  IF NOT FOUND THEN
    RAISE EXCEPTION 'verification candidate unavailable' USING ERRCODE='23514';
  END IF;
  issued := clock_timestamp();
  envelope := jsonb_build_object(
    'version','hmac-sha256-v1','key_version',selected_key,
    'purpose','materialization.attest','audience','omega_publication',
    'verifier','refinement.parquet-verifier/v1',
    'candidate_id',candidate.candidate_id,'run',candidate.materialization_run_id,
    'attempt',candidate.attempt,'tenant_id',candidate.tenant_id,
    'workspace_id',candidate.workspace_id,'dataset',candidate.dataset,
    'layer',candidate.layer,'object_uri',candidate.object_uri,
    'object_version',candidate.object_version,
    'object_checksum',candidate.object_checksum,'row_count',candidate.row_count,
    'schema_digest',candidate.schema_digest,'input_digest',candidate.input_digest,
    'contract_digest',candidate.contract_digest,
    'candidate_digest',candidate.candidate_digest,'issued_at',issued,
    'expires_at',candidate.expires_at);
  digest_value := encode(public.digest(convert_to(envelope::text,'UTF8'),'sha256'),'hex');
  SELECT encode(public.hmac(convert_to(envelope::text,'UTF8'),k.secret,'sha256'),'hex')
    INTO signature_value FROM omega_publication.attestation_signing_keys k
   WHERE k.key_version=selected_key AND k.retired_at IS NULL;
  IF signature_value IS NULL THEN
    RAISE EXCEPTION 'attestation signing unavailable';
  END IF;
  INSERT INTO omega_publication.materialization_attestations(
    materialization_run_id,tenant_id,workspace_id,dataset,layer,attempt,
    object_uri,object_version,object_checksum,row_count,schema_digest,
    input_digest,contract_digest,lineage,catalog,verifier_identity,
    attestation_digest,candidate_id,signature_version,key_version,
    attestation_signature,created_at,expires_at
  ) VALUES (candidate.materialization_run_id,candidate.tenant_id,
    candidate.workspace_id,candidate.dataset,candidate.layer,candidate.attempt,
    candidate.object_uri,candidate.object_version,candidate.object_checksum,
    candidate.row_count,candidate.schema_digest,candidate.input_digest,
    candidate.contract_digest,candidate.lineage,candidate.catalog,
    'refinement.parquet-verifier/v1',digest_value,candidate.candidate_id,
    'hmac-sha256-v1',selected_key,signature_value,issued,candidate.expires_at)
  ON CONFLICT(materialization_run_id,attestation_digest) DO NOTHING;
  UPDATE omega_publication.materialization_verification_candidates
     SET status='attested' WHERE candidate_id=candidate.candidate_id;
  RETURN digest_value;
END $$;

CREATE OR REPLACE FUNCTION omega_publication.verify_attestation(p_id uuid)
RETURNS boolean LANGUAGE sql STABLE SECURITY DEFINER
SET search_path=pg_catalog,omega_publication AS $$
  SELECT EXISTS(
    SELECT 1 FROM omega_publication.materialization_attestations a
    JOIN omega_publication.materialization_verification_candidates c
      ON c.candidate_id=a.candidate_id
    JOIN omega_publication.attestation_signing_keys k
      ON k.key_version=a.key_version
   WHERE a.attestation_id=p_id AND a.signature_version='hmac-sha256-v1'
     AND a.attestation_digest=encode(public.digest(convert_to(jsonb_build_object(
       'version',a.signature_version,'key_version',a.key_version,
       'purpose','materialization.attest','audience','omega_publication',
       'verifier',a.verifier_identity,'candidate_id',c.candidate_id,
       'run',c.materialization_run_id,'attempt',c.attempt,
       'tenant_id',c.tenant_id,'workspace_id',c.workspace_id,
       'dataset',c.dataset,'layer',c.layer,'object_uri',c.object_uri,
       'object_version',c.object_version,'object_checksum',c.object_checksum,
       'row_count',c.row_count,'schema_digest',c.schema_digest,
       'input_digest',c.input_digest,'contract_digest',c.contract_digest,
       'candidate_digest',c.candidate_digest,'issued_at',a.created_at,
       'expires_at',a.expires_at)::text,'UTF8'),'sha256'),'hex')
     AND a.attestation_signature=encode(public.hmac(convert_to(jsonb_build_object(
       'version',a.signature_version,'key_version',a.key_version,
       'purpose','materialization.attest','audience','omega_publication',
       'verifier',a.verifier_identity,'candidate_id',c.candidate_id,
       'run',c.materialization_run_id,'attempt',c.attempt,
       'tenant_id',c.tenant_id,'workspace_id',c.workspace_id,
       'dataset',c.dataset,'layer',c.layer,'object_uri',c.object_uri,
       'object_version',c.object_version,'object_checksum',c.object_checksum,
       'row_count',c.row_count,'schema_digest',c.schema_digest,
       'input_digest',c.input_digest,'contract_digest',c.contract_digest,
       'candidate_digest',c.candidate_digest,'issued_at',a.created_at,
       'expires_at',a.expires_at)::text,'UTF8'),k.secret,'sha256'),'hex')
  )
$$;

CREATE OR REPLACE FUNCTION omega_publication.reopen_prepared_materialization(
  p_run uuid,p_reason text
) RETURNS TABLE(
  object_uri text,object_version text,object_checksum text,row_count bigint,
  staging_table text,gold_table text,lineage jsonb,catalog jsonb,
  expected_head_run_id uuid
) LANGUAGE plpgsql SECURITY DEFINER
SET search_path=pg_catalog,omega_publication AS $$
DECLARE
  run_row omega_publication.materialization_runs%ROWTYPE;
  evidence_row omega_publication.materialization_evidence%ROWTYPE;
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
  IF run_row.status<>'prepared' THEN
    RAISE EXCEPTION 'prepared materialization is not reopenable'
      USING ERRCODE='23514';
  END IF;
  IF EXISTS(SELECT 1 FROM omega_publication.dataset_publication_heads
             WHERE materialization_run_id=p_run)
     OR EXISTS(SELECT 1 FROM omega_publication.materialization_receipts
                WHERE materialization_run_id=p_run) THEN
    RAISE EXCEPTION 'published materialization cannot be reopened'
      USING ERRCODE='23514';
  END IF;
  SELECT * INTO evidence_row FROM omega_publication.materialization_evidence
   WHERE materialization_run_id=p_run FOR UPDATE;
  IF NOT FOUND THEN
    RAISE EXCEPTION 'prepared materialization evidence is unavailable'
      USING ERRCODE='23514';
  END IF;
  IF (evidence_row.tenant_id,evidence_row.workspace_id,evidence_row.dataset,
      evidence_row.layer,evidence_row.object_uri,evidence_row.object_version,
      evidence_row.object_checksum,evidence_row.row_count)
     IS DISTINCT FROM
     (run_row.tenant_id,run_row.workspace_id,run_row.dataset,run_row.layer,
      run_row.object_uri,run_row.object_version,run_row.object_checksum,
      run_row.row_count) THEN
    RAISE EXCEPTION 'prepared materialization evidence mismatch'
      USING ERRCODE='23514';
  END IF;
  INSERT INTO omega_publication.materialization_recovery_events(
    materialization_run_id,tenant_id,workspace_id,attempt,reason,evidence
  ) VALUES(
    p_run,run_row.tenant_id,run_row.workspace_id,run_row.attempt,
    left(COALESCE(NULLIF(p_reason,''),'prepared_revalidation'),240),
    to_jsonb(evidence_row)
  );
  UPDATE omega_publication.materialization_attestations
     SET invalidated_at=COALESCE(invalidated_at,clock_timestamp())
   WHERE materialization_run_id=p_run;
  UPDATE omega_publication.materialization_verification_candidates
     SET status='expired'
   WHERE materialization_run_id=p_run AND status='pending';
  DELETE FROM omega_publication.materialization_evidence
   WHERE materialization_run_id=p_run;
  UPDATE omega_publication.materialization_runs
     SET status='reserved',attempt=attempt+1,prepared_at=NULL,
         schema_digest=NULL,evidence_digest=NULL,recovery_reason=left(p_reason,240)
   WHERE materialization_run_id=p_run;
  RETURN QUERY SELECT run_row.object_uri,run_row.object_version,
    run_row.object_checksum,run_row.row_count,run_row.staging_table,
    run_row.gold_table,evidence_row.lineage,evidence_row.catalog,
    run_row.expected_head_run_id;
END $$;

ALTER FUNCTION omega_publication.reopen_prepared_materialization(uuid,text)
  OWNER TO omega_gold_owner;
