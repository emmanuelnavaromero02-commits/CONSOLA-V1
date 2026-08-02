BEGIN;

ALTER TABLE omega_publication.materialization_runs
  ADD COLUMN IF NOT EXISTS attempt integer NOT NULL DEFAULT 1,
  ADD COLUMN IF NOT EXISTS recovery_reason text;
ALTER TABLE omega_publication.materialization_runs
  DROP CONSTRAINT IF EXISTS materialization_runs_status_check;
ALTER TABLE omega_publication.materialization_runs
  ADD CONSTRAINT materialization_runs_status_check CHECK (
    status IN ('reserved','prepared','published','failed',
               'recoverable_failed','legacy_unverified')
  );

CREATE TABLE IF NOT EXISTS omega_publication.materialization_attestations (
  attestation_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  materialization_run_id uuid NOT NULL
    REFERENCES omega_publication.materialization_runs(materialization_run_id),
  tenant_id uuid NOT NULL, workspace_id uuid NOT NULL,
  dataset text NOT NULL, layer text NOT NULL CHECK (layer IN ('silver','gold')),
  attempt integer NOT NULL CHECK (attempt > 0),
  object_uri text NOT NULL CHECK (object_uri ~ '^(s3|gs)://'),
  object_checksum text NOT NULL CHECK (object_checksum ~ '^[0-9a-f]{64}$'),
  row_count bigint NOT NULL CHECK (row_count >= 0),
  schema_digest text NOT NULL CHECK (schema_digest ~ '^[0-9a-f]{64}$'),
  input_digest text NOT NULL CHECK (input_digest ~ '^[0-9a-f]{64}$'),
  contract_digest text NOT NULL CHECK (contract_digest ~ '^[0-9a-f]{64}$'),
  lineage jsonb NOT NULL CHECK (jsonb_typeof(lineage)='object'),
  catalog jsonb NOT NULL CHECK (jsonb_typeof(catalog)='array'),
  verifier_identity text NOT NULL CHECK (
    verifier_identity='refinement.parquet-verifier/v1'
  ),
  attestation_digest text NOT NULL CHECK (attestation_digest ~ '^[0-9a-f]{64}$'),
  created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  expires_at timestamptz NOT NULL,
  consumed_at timestamptz,
  invalidated_at timestamptz,
  UNIQUE (materialization_run_id,attestation_digest)
);
ALTER TABLE omega_publication.materialization_attestations
  ADD COLUMN IF NOT EXISTS dataset text,
  ADD COLUMN IF NOT EXISTS layer text,
  ADD COLUMN IF NOT EXISTS attempt integer,
  ADD COLUMN IF NOT EXISTS schema_digest text,
  ADD COLUMN IF NOT EXISTS input_digest text,
  ADD COLUMN IF NOT EXISTS contract_digest text,
  ADD COLUMN IF NOT EXISTS verifier_identity text,
  ADD COLUMN IF NOT EXISTS expires_at timestamptz,
  ADD COLUMN IF NOT EXISTS invalidated_at timestamptz;
UPDATE omega_publication.materialization_attestations AS a
   SET dataset=r.dataset,
       layer=r.layer,
       attempt=r.attempt,
       schema_digest=encode(
         public.digest(convert_to(a.catalog::text,'UTF8'),'sha256'),'hex'
       ),
       input_digest=r.input_digest,
       contract_digest=r.contract_digest,
       verifier_identity='refinement.parquet-verifier/v1',
       expires_at=a.created_at+interval '15 minutes',
       invalidated_at=COALESCE(a.invalidated_at,clock_timestamp())
  FROM omega_publication.materialization_runs r
 WHERE a.materialization_run_id=r.materialization_run_id
   AND (a.dataset IS NULL OR a.layer IS NULL OR a.attempt IS NULL
     OR a.schema_digest IS NULL OR a.input_digest IS NULL
     OR a.contract_digest IS NULL OR a.verifier_identity IS NULL
     OR a.expires_at IS NULL);
ALTER TABLE omega_publication.materialization_attestations
  ALTER COLUMN dataset SET NOT NULL,
  ALTER COLUMN layer SET NOT NULL,
  ALTER COLUMN attempt SET NOT NULL,
  ALTER COLUMN schema_digest SET NOT NULL,
  ALTER COLUMN input_digest SET NOT NULL,
  ALTER COLUMN contract_digest SET NOT NULL,
  ALTER COLUMN verifier_identity SET NOT NULL,
  ALTER COLUMN expires_at SET NOT NULL;
ALTER TABLE omega_publication.materialization_attestations
  DROP CONSTRAINT IF EXISTS materialization_attestations_layer_check,
  DROP CONSTRAINT IF EXISTS materialization_attestations_verifier_identity_check;
ALTER TABLE omega_publication.materialization_attestations
  ADD CONSTRAINT materialization_attestations_layer_check
    CHECK (layer IN ('silver','gold')),
  ADD CONSTRAINT materialization_attestations_verifier_identity_check
    CHECK (verifier_identity='refinement.parquet-verifier/v1');
ALTER TABLE omega_publication.materialization_evidence
  ADD COLUMN IF NOT EXISTS attestation_id uuid;
ALTER TABLE omega_publication.materialization_evidence
  DROP CONSTRAINT IF EXISTS materialization_evidence_attestation_id_fkey;
ALTER TABLE omega_publication.materialization_evidence
  ADD CONSTRAINT materialization_evidence_attestation_id_fkey
  FOREIGN KEY (attestation_id)
  REFERENCES omega_publication.materialization_attestations(attestation_id);
CREATE TABLE IF NOT EXISTS omega_publication.materialization_recovery_events (
  recovery_event_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  materialization_run_id uuid NOT NULL,
  tenant_id uuid NOT NULL, workspace_id uuid NOT NULL,
  attempt integer NOT NULL CHECK (attempt > 0),
  reason text NOT NULL, evidence jsonb NOT NULL,
  created_at timestamptz NOT NULL DEFAULT clock_timestamp()
);

ALTER TABLE omega_publication.materialization_attestations OWNER TO omega_gold_owner;
ALTER TABLE omega_publication.materialization_recovery_events OWNER TO omega_gold_owner;
ALTER TABLE omega_publication.materialization_attestations ENABLE ROW LEVEL SECURITY;
ALTER TABLE omega_publication.materialization_attestations FORCE ROW LEVEL SECURITY;
ALTER TABLE omega_publication.materialization_recovery_events ENABLE ROW LEVEL SECURITY;
ALTER TABLE omega_publication.materialization_recovery_events FORCE ROW LEVEL SECURITY;

DO $$
DECLARE table_name text;
BEGIN
  FOREACH table_name IN ARRAY ARRAY[
    'materialization_attestations','materialization_recovery_events'
  ] LOOP
    EXECUTE format('DROP POLICY IF EXISTS %I ON omega_publication.%I',
                   table_name || '_scope',table_name);
    EXECUTE format(
      'CREATE POLICY %I ON omega_publication.%I '
      'TO omega_gold_owner,omega_refinement_gold,omega_gold_publisher '
      'USING (tenant_id::text=NULLIF(current_setting(''app.tenant_id'',true),'''') '
      'AND workspace_id::text=NULLIF(current_setting(''app.workspace_id'',true),'''')) '
      'WITH CHECK (tenant_id::text=NULLIF(current_setting(''app.tenant_id'',true),'''') '
      'AND workspace_id::text=NULLIF(current_setting(''app.workspace_id'',true),''''))',
      table_name || '_scope',table_name
    );
  END LOOP;
END $$;

GRANT SELECT ON omega_publication.materialization_attestations,
  omega_publication.materialization_recovery_events
  TO omega_refinement_gold,omega_gold_publisher;
REVOKE INSERT,UPDATE,DELETE,TRUNCATE ON
  omega_publication.materialization_attestations,
  omega_publication.materialization_recovery_events
  FROM omega_refinement_gold,omega_gold_publisher;

DROP FUNCTION IF EXISTS omega_publication.gold_compatibility_relation(text);
CREATE OR REPLACE FUNCTION omega_publication.gold_compatibility_relation(
  p_tenant uuid,p_workspace uuid,p_dataset text
) RETURNS text LANGUAGE plpgsql IMMUTABLE STRICT SET search_path=pg_catalog AS $$
DECLARE scope_digest text;
BEGIN
  IF p_dataset !~ '^[A-Za-z_][A-Za-z0-9_]{0,127}$' THEN
    RAISE EXCEPTION 'invalid Gold dataset name' USING ERRCODE='22023';
  END IF;
  scope_digest := encode(public.digest(convert_to(
    p_tenant::text || ':' || p_workspace::text || ':' || p_dataset,'UTF8'
  ),'sha256'),'hex');
  RETURN 'gold_' || substr(p_dataset,1,38) || '_' || substr(scope_digest,1,16);
END $$;

DO $$
DECLARE mapping record; target text; column_list text;
BEGIN
  FOR mapping IN SELECT * FROM omega_publication.dataset_gold_relations LOOP
    target := omega_publication.gold_compatibility_relation(
      mapping.tenant_id,mapping.workspace_id,mapping.dataset
    );
    IF target <> mapping.relation_name
       AND to_regclass(format('public.%I',mapping.relation_name)) IS NOT NULL
       AND to_regclass(format('public.%I',target)) IS NULL THEN
      EXECUTE format('CREATE TABLE public.%I (LIKE public.%I INCLUDING DEFAULTS)',
                     target,mapping.relation_name);
      SELECT string_agg(format('%I',a.attname),',' ORDER BY a.attnum)
        INTO column_list FROM pg_attribute a
       WHERE a.attrelid=format('public.%I',mapping.relation_name)::regclass
         AND a.attnum>0 AND NOT a.attisdropped;
      EXECUTE format(
        'INSERT INTO public.%I (%s) SELECT %s FROM public.%I '
        'WHERE tenant_id::text=%L AND workspace_id::text=%L',
        target,column_list,column_list,mapping.relation_name,
        mapping.tenant_id::text,mapping.workspace_id::text
      );
      EXECUTE format('ALTER TABLE public.%I OWNER TO omega_gold_owner',target);
    END IF;
    UPDATE omega_publication.dataset_gold_relations
       SET relation_name=target
     WHERE tenant_id=mapping.tenant_id AND workspace_id=mapping.workspace_id
       AND dataset=mapping.dataset;
  END LOOP;
END $$;

ALTER TABLE omega_publication.dataset_gold_relations
  DROP CONSTRAINT IF EXISTS dataset_gold_relations_tenant_id_workspace_id_relation_name_key;
CREATE UNIQUE INDEX IF NOT EXISTS dataset_gold_relations_physical_name_key
  ON omega_publication.dataset_gold_relations(relation_name);

COMMIT;

\ir 41_staged_publication_functions.sql
\ir 42_staged_publication_cas.sql

INSERT INTO schema_migrations(filename,applied_at)
VALUES ('gold/43_staged_publication_authority.sql',NOW())
ON CONFLICT (filename) DO NOTHING;
