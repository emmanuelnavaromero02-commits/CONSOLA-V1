CREATE EXTENSION IF NOT EXISTS pgcrypto;

CREATE SCHEMA IF NOT EXISTS omega_publication AUTHORIZATION omega_gold_owner;
CREATE SCHEMA IF NOT EXISTS omega_publication_stage AUTHORIZATION omega_gold_owner;
CREATE SCHEMA IF NOT EXISTS omega_publication_gold AUTHORIZATION omega_gold_owner;
CREATE SCHEMA IF NOT EXISTS omega_publication_views AUTHORIZATION omega_gold_owner;
CREATE SCHEMA IF NOT EXISTS omega_publication_legacy AUTHORIZATION omega_gold_owner;

REVOKE ALL ON SCHEMA omega_publication, omega_publication_stage,
  omega_publication_gold, omega_publication_views, omega_publication_legacy
  FROM PUBLIC,omega_refinement_gold,omega_gold_publisher,omega_gold_verifier;
GRANT USAGE ON SCHEMA omega_publication, omega_publication_gold,
  omega_publication_views
  TO omega_refinement_gold,omega_gold_publisher,omega_gold_verifier;
GRANT USAGE ON SCHEMA omega_publication_stage TO omega_gold_publisher;

CREATE TABLE IF NOT EXISTS omega_publication.materialization_runs (
    materialization_run_id uuid PRIMARY KEY,
    tenant_id uuid NOT NULL,
    workspace_id uuid NOT NULL,
    dataset text NOT NULL CHECK (dataset ~ '^[A-Za-z_][A-Za-z0-9_]{0,127}$'),
    layer text NOT NULL CHECK (layer IN ('silver', 'gold')),
    input_digest text NOT NULL CHECK (input_digest ~ '^[0-9a-f]{64}$'),
    contract_digest text NOT NULL CHECK (contract_digest ~ '^[0-9a-f]{64}$'),
    expected_head_run_id uuid,
    expected_head_generation bigint CHECK (
      expected_head_generation IS NULL OR expected_head_generation > 0
    ),
    object_uri text,
    object_version text,
    object_checksum text CHECK (object_checksum IS NULL OR object_checksum ~ '^[0-9a-f]{64}$'),
    row_count bigint CHECK (row_count IS NULL OR row_count >= 0),
    schema_digest text CHECK (schema_digest IS NULL OR schema_digest ~ '^[0-9a-f]{64}$'),
    evidence_digest text CHECK (evidence_digest IS NULL OR evidence_digest ~ '^[0-9a-f]{64}$'),
    gold_table text CHECK (
      gold_table IS NULL OR gold_table ~ '^(gold_[A-Za-z0-9_]+|run_[0-9a-f]{32})$'
    ),
    staging_table text CHECK (staging_table IS NULL OR staging_table ~ '^run_[0-9a-f]{32}$'),
    status text NOT NULL DEFAULT 'reserved'
      CHECK (status IN ('reserved', 'prepared', 'published', 'failed',
                        'recoverable_failed', 'legacy_unverified')),
    attempt integer NOT NULL DEFAULT 1 CHECK (attempt > 0),
    recovery_reason text,
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    prepared_at timestamptz,
    published_at timestamptz
);

CREATE UNIQUE INDEX IF NOT EXISTS materialization_runs_scope_run_idx
  ON omega_publication.materialization_runs
  (tenant_id, workspace_id, dataset, layer, materialization_run_id);

CREATE TABLE IF NOT EXISTS omega_publication.dataset_publication_heads (
    tenant_id uuid NOT NULL,
    workspace_id uuid NOT NULL,
    dataset text NOT NULL,
    layer text NOT NULL CHECK (layer IN ('silver', 'gold')),
    materialization_run_id uuid NOT NULL
      REFERENCES omega_publication.materialization_runs(materialization_run_id),
    generation bigint NOT NULL CHECK (generation > 0),
    published_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY (tenant_id, workspace_id, dataset, layer)
);

CREATE TABLE IF NOT EXISTS omega_publication.materialization_receipts (
    receipt_id uuid PRIMARY KEY,
    materialization_run_id uuid NOT NULL UNIQUE
      REFERENCES omega_publication.materialization_runs(materialization_run_id),
    tenant_id uuid NOT NULL,
    workspace_id uuid NOT NULL,
    dataset text NOT NULL,
    layer text NOT NULL,
    input_digest text NOT NULL,
    contract_digest text NOT NULL,
    object_checksum text NOT NULL,
    object_version text NOT NULL,
    schema_digest text NOT NULL,
    evidence_digest text NOT NULL,
    row_count bigint NOT NULL CHECK (row_count >= 0),
    generation bigint NOT NULL CHECK (generation > 0),
    created_at timestamptz NOT NULL DEFAULT clock_timestamp()
);

CREATE TABLE IF NOT EXISTS omega_publication.materialization_evidence (
    materialization_run_id uuid PRIMARY KEY
      REFERENCES omega_publication.materialization_runs(materialization_run_id),
    tenant_id uuid NOT NULL,
    workspace_id uuid NOT NULL,
    dataset text NOT NULL,
    layer text NOT NULL CHECK (layer IN ('silver', 'gold')),
    object_uri text NOT NULL CHECK (object_uri ~ '^(s3|gs)://'),
    object_version text NOT NULL,
    object_checksum text NOT NULL CHECK (object_checksum ~ '^[0-9a-f]{64}$'),
    row_count bigint NOT NULL CHECK (row_count >= 0),
    schema_digest text NOT NULL CHECK (schema_digest ~ '^[0-9a-f]{64}$'),
    evidence_digest text NOT NULL CHECK (evidence_digest ~ '^[0-9a-f]{64}$'),
    attestation_id uuid,
    lineage jsonb NOT NULL CHECK (jsonb_typeof(lineage)='object'),
    catalog jsonb NOT NULL CHECK (jsonb_typeof(catalog)='array'),
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    UNIQUE (tenant_id, workspace_id, dataset, layer, materialization_run_id)
);

CREATE TABLE IF NOT EXISTS omega_publication.materialization_attestations (
    attestation_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    materialization_run_id uuid NOT NULL
      REFERENCES omega_publication.materialization_runs(materialization_run_id),
    tenant_id uuid NOT NULL,
    workspace_id uuid NOT NULL,
    dataset text NOT NULL,
    layer text NOT NULL CHECK (layer IN ('silver', 'gold')),
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
    verifier_identity text NOT NULL CHECK (
      verifier_identity = 'refinement.parquet-verifier/v1'
    ),
    attestation_digest text NOT NULL CHECK (attestation_digest ~ '^[0-9a-f]{64}$'),
    candidate_id uuid NOT NULL,
    signature_version text NOT NULL DEFAULT 'hmac-sha256-v1',
    key_version text NOT NULL DEFAULT 'v1',
    attestation_signature text NOT NULL CHECK (
      attestation_signature ~ '^[0-9a-f]{64}$'
    ),
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    expires_at timestamptz NOT NULL,
    consumed_at timestamptz,
    invalidated_at timestamptz,
    UNIQUE (materialization_run_id, attestation_digest)
);

\ir fragments/40_verification_schema.sql

ALTER TABLE omega_publication.materialization_evidence
  DROP CONSTRAINT IF EXISTS materialization_evidence_attestation_id_fkey;
ALTER TABLE omega_publication.materialization_evidence
  ADD CONSTRAINT materialization_evidence_attestation_id_fkey
  FOREIGN KEY (attestation_id)
  REFERENCES omega_publication.materialization_attestations(attestation_id);

CREATE TABLE IF NOT EXISTS omega_publication.materialization_recovery_events (
    recovery_event_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    materialization_run_id uuid NOT NULL,
    tenant_id uuid NOT NULL,
    workspace_id uuid NOT NULL,
    attempt integer NOT NULL CHECK (attempt > 0),
    reason text NOT NULL,
    evidence jsonb NOT NULL,
    created_at timestamptz NOT NULL DEFAULT clock_timestamp()
);

CREATE TABLE IF NOT EXISTS omega_publication.dataset_gold_relations (
    tenant_id uuid NOT NULL,
    workspace_id uuid NOT NULL,
    dataset text NOT NULL CHECK (dataset ~ '^[A-Za-z_][A-Za-z0-9_]{0,127}$'),
    relation_name text NOT NULL CHECK (
      relation_name ~ '^gold_[A-Za-z0-9_]+$' AND octet_length(relation_name) <= 63
    ),
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY (tenant_id, workspace_id, dataset),
    UNIQUE (tenant_id, workspace_id, relation_name)
);

ALTER TABLE omega_publication.materialization_runs ENABLE ROW LEVEL SECURITY;
ALTER TABLE omega_publication.materialization_runs FORCE ROW LEVEL SECURITY;
ALTER TABLE omega_publication.dataset_publication_heads ENABLE ROW LEVEL SECURITY;
ALTER TABLE omega_publication.dataset_publication_heads FORCE ROW LEVEL SECURITY;
ALTER TABLE omega_publication.materialization_receipts ENABLE ROW LEVEL SECURITY;
ALTER TABLE omega_publication.materialization_receipts FORCE ROW LEVEL SECURITY;
ALTER TABLE omega_publication.materialization_evidence ENABLE ROW LEVEL SECURITY;
ALTER TABLE omega_publication.materialization_evidence FORCE ROW LEVEL SECURITY;
ALTER TABLE omega_publication.materialization_attestations ENABLE ROW LEVEL SECURITY;
ALTER TABLE omega_publication.materialization_attestations FORCE ROW LEVEL SECURITY;
ALTER TABLE omega_publication.materialization_verification_candidates ENABLE ROW LEVEL SECURITY;
ALTER TABLE omega_publication.materialization_verification_candidates FORCE ROW LEVEL SECURITY;
ALTER TABLE omega_publication.materialization_recovery_events ENABLE ROW LEVEL SECURITY;
ALTER TABLE omega_publication.materialization_recovery_events FORCE ROW LEVEL SECURITY;
ALTER TABLE omega_publication.dataset_gold_relations ENABLE ROW LEVEL SECURITY;
ALTER TABLE omega_publication.dataset_gold_relations FORCE ROW LEVEL SECURITY;

DO $$
DECLARE
  table_name text;
BEGIN
  FOREACH table_name IN ARRAY ARRAY[
    'materialization_runs', 'dataset_publication_heads', 'materialization_receipts',
    'materialization_evidence', 'materialization_attestations',
    'materialization_verification_candidates',
    'materialization_recovery_events', 'dataset_gold_relations'
  ] LOOP
    EXECUTE format('DROP POLICY IF EXISTS %I ON omega_publication.%I', table_name || '_scope', table_name);
    EXECUTE format(
      'CREATE POLICY %I ON omega_publication.%I TO omega_gold_owner,omega_refinement_gold,omega_gold_publisher,omega_gold_verifier '
      'USING (tenant_id::text = NULLIF(current_setting(''app.tenant_id'', true), '''') '
      'AND workspace_id::text = NULLIF(current_setting(''app.workspace_id'', true), '''')) '
      'WITH CHECK (tenant_id::text = NULLIF(current_setting(''app.tenant_id'', true), '''') '
      'AND workspace_id::text = NULLIF(current_setting(''app.workspace_id'', true), ''''))',
      table_name || '_scope', table_name
    );
  END LOOP;
END $$;

-- Legacy tables remain visible only while the scoped head still points at the
-- backfilled legacy run. Publishing a staged run immediately retires those rows.
DO $$
DECLARE
  relation record;
  owner_policy text;
  reader_policy text;
  dataset_name text;
BEGIN
  FOR relation IN
    SELECT c.relname FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
     WHERE n.nspname='public' AND c.relname LIKE 'gold\_%' ESCAPE '\'
       AND c.relkind IN ('r','p')
       AND EXISTS (SELECT 1 FROM pg_attribute a WHERE a.attrelid=c.oid
                    AND a.attname='tenant_id' AND NOT a.attisdropped)
       AND EXISTS (SELECT 1 FROM pg_attribute a WHERE a.attrelid=c.oid
                    AND a.attname='workspace_id' AND NOT a.attisdropped)
  LOOP
    owner_policy := CASE
      WHEN octet_length(relation.relname || '_publication_owner') <= 63
      THEN relation.relname || '_publication_owner'
      ELSE 'publication_owner'
    END;
    reader_policy := CASE
      WHEN octet_length(relation.relname || '_tenant_workspace_rls') <= 63
      THEN relation.relname || '_tenant_workspace_rls'
      ELSE 'tenant_workspace_rls'
    END;
    SELECT min(mapping.dataset) INTO dataset_name
      FROM omega_publication.dataset_gold_relations AS mapping
     WHERE mapping.relation_name=relation.relname;
    dataset_name := COALESCE(dataset_name,substr(relation.relname,6));
    EXECUTE format('DROP POLICY IF EXISTS %I ON public.%I',
                   reader_policy, relation.relname);
    EXECUTE format('DROP POLICY IF EXISTS %I ON public.%I',
                   owner_policy, relation.relname);
    EXECUTE format(
      'CREATE POLICY %I ON public.%I TO omega_gold_owner USING (true) WITH CHECK (true)',
      owner_policy, relation.relname
    );
    EXECUTE format(
      'CREATE POLICY %I ON public.%I FOR SELECT TO omega_refinement_gold USING ('
      'public.omega_gold_workspace_matches(tenant_id::text,workspace_id::text) AND '
      'EXISTS (SELECT 1 FROM omega_publication.dataset_publication_heads h '
      'WHERE h.tenant_id::text=NULLIF(current_setting(''app.tenant_id'',true),'''') '
      'AND h.workspace_id::text=NULLIF(current_setting(''app.workspace_id'',true),'''') '
      'AND h.dataset=%L AND h.layer=''gold''))',
      reader_policy,relation.relname,dataset_name
    );
  END LOOP;
END $$;

ALTER TABLE omega_publication.materialization_runs OWNER TO omega_gold_owner;
ALTER TABLE omega_publication.dataset_publication_heads OWNER TO omega_gold_owner;
ALTER TABLE omega_publication.materialization_receipts OWNER TO omega_gold_owner;
ALTER TABLE omega_publication.materialization_evidence OWNER TO omega_gold_owner;
ALTER TABLE omega_publication.materialization_attestations OWNER TO omega_gold_owner;
ALTER TABLE omega_publication.materialization_verification_candidates OWNER TO omega_gold_owner;
ALTER TABLE omega_publication.attestation_signing_keys OWNER TO omega_gold_owner;
ALTER TABLE omega_publication.materialization_recovery_events OWNER TO omega_gold_owner;
ALTER TABLE omega_publication.dataset_gold_relations OWNER TO omega_gold_owner;
GRANT SELECT ON ALL TABLES IN SCHEMA omega_publication
  TO omega_refinement_gold, omega_gold_publisher;
REVOKE INSERT, UPDATE, DELETE, TRUNCATE ON ALL TABLES IN SCHEMA omega_publication
  FROM omega_refinement_gold,omega_gold_publisher,omega_gold_verifier;
GRANT SELECT ON omega_publication.materialization_verification_candidates
  TO omega_gold_verifier;

-- Unscoped legacy relations cannot be assigned to a workspace safely. Preserve
-- them byte-for-byte in a schema inaccessible to runtime readers.
DO $$
DECLARE relation record;
BEGIN
  FOR relation IN
    SELECT c.relname
      FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
     WHERE n.nspname='public' AND c.relname LIKE 'gold\_%' ESCAPE '\'
       AND c.relkind IN ('r','p')
       AND NOT (
         EXISTS (SELECT 1 FROM pg_attribute a WHERE a.attrelid=c.oid AND a.attname='tenant_id' AND NOT a.attisdropped)
         AND EXISTS (SELECT 1 FROM pg_attribute a WHERE a.attrelid=c.oid AND a.attname='workspace_id' AND NOT a.attisdropped)
       )
  LOOP
    EXECUTE format('ALTER TABLE public.%I SET SCHEMA omega_publication_legacy', relation.relname);
  END LOOP;
END $$;

-- Existing scoped Gold remains readable but is explicitly legacy/unverified.
-- No object checksum, lineage or approval is invented during upgrade.
DO $$
DECLARE
  relation record;
  dataset_name text;
BEGIN
  FOR relation IN
    SELECT c.relname
      FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
     WHERE n.nspname='public' AND c.relname LIKE 'gold\_%' ESCAPE '\'
       AND c.relkind IN ('r','p')
       AND EXISTS (SELECT 1 FROM pg_attribute a WHERE a.attrelid=c.oid AND a.attname='tenant_id' AND NOT a.attisdropped)
       AND EXISTS (SELECT 1 FROM pg_attribute a WHERE a.attrelid=c.oid AND a.attname='workspace_id' AND NOT a.attisdropped)
  LOOP
    SELECT min(dataset) INTO dataset_name
      FROM omega_publication.dataset_gold_relations
     WHERE relation_name=relation.relname;
    dataset_name := COALESCE(dataset_name, substr(relation.relname, 6));
    EXECUTE format($sql$
      WITH scoped AS (
        SELECT tenant_id::uuid AS tenant_id, workspace_id::uuid AS workspace_id,
               count(*) AS row_count
          FROM public.%I
         WHERE tenant_id::text ~ '^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$'
           AND workspace_id::text ~ '^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$'
         GROUP BY tenant_id::uuid, workspace_id::uuid
      ), identified AS (
        SELECT *, md5(tenant_id::text || ':' || workspace_id::text || ':%s:gold') AS h
          FROM scoped
      ), runs AS (
        INSERT INTO omega_publication.materialization_runs (
          materialization_run_id, tenant_id, workspace_id, dataset, layer,
          input_digest, contract_digest, gold_table, row_count, status
        ) SELECT
          (substr(h,1,8)||'-'||substr(h,9,4)||'-'||substr(h,13,4)||'-'||substr(h,17,4)||'-'||substr(h,21,12))::uuid,
          tenant_id, workspace_id, %L, 'gold', repeat('0',64), repeat('0',64), %L, row_count,
          'legacy_unverified'
        FROM identified
        WHERE NOT EXISTS (
          SELECT 1 FROM omega_publication.dataset_publication_heads h
           WHERE h.tenant_id=identified.tenant_id
             AND h.workspace_id=identified.workspace_id
             AND h.dataset=%L AND h.layer='gold'
        )
        ON CONFLICT (materialization_run_id) DO NOTHING
        RETURNING materialization_run_id, tenant_id, workspace_id
      ), mappings AS (
        INSERT INTO omega_publication.dataset_gold_relations (
          tenant_id, workspace_id, dataset, relation_name
        ) SELECT tenant_id, workspace_id, %L, %L FROM identified
        ON CONFLICT DO NOTHING
      )
      INSERT INTO omega_publication.dataset_publication_heads (
        tenant_id, workspace_id, dataset, layer, materialization_run_id, generation
      ) SELECT tenant_id, workspace_id, %L, 'gold', materialization_run_id, 1 FROM runs
      ON CONFLICT (tenant_id, workspace_id, dataset, layer) DO NOTHING
    $sql$, relation.relname, dataset_name, dataset_name, relation.relname,
            dataset_name, dataset_name, relation.relname, dataset_name);
  END LOOP;
END $$;

INSERT INTO schema_migrations(filename,applied_at)
VALUES ('gold/40_staged_publication_schema.sql',NOW())
ON CONFLICT (filename) DO NOTHING;
