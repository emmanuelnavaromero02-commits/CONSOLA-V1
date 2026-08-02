CREATE OR REPLACE FUNCTION omega_publication.assert_current_scope(
  p_tenant uuid,p_workspace uuid
) RETURNS void LANGUAGE plpgsql SECURITY DEFINER
SET search_path=pg_catalog AS $$
BEGIN
  IF p_tenant::text IS DISTINCT FROM
       NULLIF(current_setting('app.tenant_id',true),'')
     OR p_workspace::text IS DISTINCT FROM
       NULLIF(current_setting('app.workspace_id',true),'') THEN
    RAISE EXCEPTION 'publication scope mismatch' USING ERRCODE='42501';
  END IF;
END $$;
ALTER FUNCTION omega_publication.assert_current_scope(uuid,uuid)
  OWNER TO omega_gold_owner;
REVOKE ALL ON FUNCTION omega_publication.assert_current_scope(uuid,uuid)
  FROM PUBLIC,omega_refinement_gold,omega_gold_publisher,omega_gold_verifier;

CREATE TABLE IF NOT EXISTS omega_publication.attestation_signing_keys (
    key_version text PRIMARY KEY,
    secret bytea NOT NULL CHECK (octet_length(secret) >= 32),
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    retired_at timestamptz
);
INSERT INTO omega_publication.attestation_signing_keys(key_version,secret)
VALUES ('v1',gen_random_bytes(32)) ON CONFLICT (key_version) DO NOTHING;
REVOKE ALL ON omega_publication.attestation_signing_keys FROM PUBLIC,
  omega_refinement_gold,omega_gold_publisher,omega_gold_verifier;

CREATE TABLE IF NOT EXISTS omega_publication.materialization_verification_candidates (
    candidate_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    materialization_run_id uuid NOT NULL
      REFERENCES omega_publication.materialization_runs(materialization_run_id),
    tenant_id uuid NOT NULL,
    workspace_id uuid NOT NULL,
    dataset text NOT NULL,
    layer text NOT NULL CHECK (layer IN ('silver','gold')),
    attempt integer NOT NULL CHECK (attempt > 0),
    object_uri text NOT NULL CHECK (object_uri ~ '^(s3|gs)://'),
    object_version text NOT NULL,
    object_checksum text NOT NULL CHECK (object_checksum ~ '^[0-9a-f]{64}$'),
    row_count bigint NOT NULL CHECK (row_count >= 0),
    schema_digest text NOT NULL CHECK (schema_digest ~ '^[0-9a-f]{64}$'),
    input_digest text NOT NULL CHECK (input_digest ~ '^[0-9a-f]{64}$'),
    contract_digest text NOT NULL CHECK (contract_digest ~ '^[0-9a-f]{64}$'),
    lineage jsonb NOT NULL CHECK (jsonb_typeof(lineage)='object'),
    catalog jsonb NOT NULL CHECK (jsonb_typeof(catalog)='array'),
    candidate_digest text NOT NULL CHECK (candidate_digest ~ '^[0-9a-f]{64}$'),
    status text NOT NULL DEFAULT 'pending'
      CHECK (status IN ('pending','attested','rejected','expired')),
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    expires_at timestamptz NOT NULL
);
ALTER TABLE omega_publication.materialization_verification_candidates
  DROP CONSTRAINT IF EXISTS
  materialization_verification_candidates_materialization_run_id_attempt_candidate_digest_key;
CREATE UNIQUE INDEX IF NOT EXISTS materialization_verification_candidates_pending_uidx
  ON omega_publication.materialization_verification_candidates(
    materialization_run_id,attempt,candidate_digest
  ) WHERE status='pending';

ALTER TABLE omega_publication.materialization_runs
  ADD COLUMN IF NOT EXISTS object_version text;
ALTER TABLE omega_publication.materialization_receipts
  ADD COLUMN IF NOT EXISTS object_version text;
ALTER TABLE omega_publication.materialization_evidence
  ADD COLUMN IF NOT EXISTS object_version text;
ALTER TABLE omega_publication.materialization_attestations
  ADD COLUMN IF NOT EXISTS object_version text,
  ADD COLUMN IF NOT EXISTS candidate_id uuid,
  ADD COLUMN IF NOT EXISTS signature_version text,
  ADD COLUMN IF NOT EXISTS key_version text,
  ADD COLUMN IF NOT EXISTS attestation_signature text;
UPDATE omega_publication.materialization_attestations
   SET invalidated_at=COALESCE(invalidated_at,clock_timestamp())
 WHERE object_version IS NULL OR candidate_id IS NULL
    OR signature_version IS NULL OR key_version IS NULL
    OR attestation_signature IS NULL;
ALTER TABLE omega_publication.materialization_attestations
  DROP CONSTRAINT IF EXISTS materialization_attestations_candidate_fkey;
ALTER TABLE omega_publication.materialization_attestations
  ADD CONSTRAINT materialization_attestations_candidate_fkey
  FOREIGN KEY(candidate_id)
  REFERENCES omega_publication.materialization_verification_candidates(candidate_id);
