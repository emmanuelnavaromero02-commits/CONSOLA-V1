-- Catalog Copilot: deterministic, zero-click cataloguing of published datasets.
--
-- After every publication refinement profiles the dataset (counts only) and
-- writes column annotations, sensitivity classifications and FK -> PK
-- relationships. Three rules shape this schema:
--
--   * Provenance is explicit. Every description, classification and edge
--     carries an origin (manual | packaged | copilot). The Copilot never
--     overwrites a manual or packaged value; the write guards live in SQL.
--   * Evidence holds counts, never values. copilot_evidence and the state
--     summary refuse value-bearing keys at the database level, so a bug in
--     the service cannot persist a sampled RFC, e-mail or salary.
--   * Refinement cannot DELETE catalog rows (25_service_roles.sql), so a
--     rejected or stale Copilot edge is a status tombstone, which is also
--     what keeps a rejected edge from ever being proposed again.
--
-- Safe on a live database: ADD COLUMN IF NOT EXISTS with constant defaults,
-- bounded backfills, CHECKs added NOT VALID and validated afterwards.

-- ---------------------------------------------------------------------------
-- Workspace-scoped uniqueness only
-- ---------------------------------------------------------------------------
-- 13_data_catalog.sql declared UNIQUE (from_dataset, from_column, to_dataset,
-- to_column); Postgres truncated its name, so 99w's drop of the untruncated
-- name was a no-op, and 67 added data_relationships_edge_uq on the same
-- columns. Both are global: the second workspace that registers the same edge
-- between identically named datasets fails with a unique violation. Beta
-- databases created by 19_operational_stability_hotfix.sql carry the same
-- global key as a composite primary key. Only the workspace-scoped partial
-- index (data_relationships_scoped_key) and the legacy partial index for
-- unscoped rows (data_relationships_legacy_key) stay.
ALTER TABLE data_relationships
    DROP CONSTRAINT IF EXISTS data_relationships_from_dataset_from_column_to_dataset_to_c_key;
ALTER TABLE data_relationships
    DROP CONSTRAINT IF EXISTS data_relationships_from_dataset_from_column_to_dataset_to_column_key;
DROP INDEX IF EXISTS data_relationships_edge_uq;

DO $catalog_copilot_primary_keys$
DECLARE
    tbl TEXT;
    pk_name TEXT;
    pk_columns TEXT[];
BEGIN
    FOREACH tbl IN ARRAY ARRAY['data_relationships', 'data_catalog']
    LOOP
        SELECT c.conname,
               array_agg(a.attname::text ORDER BY a.attnum)
          INTO pk_name, pk_columns
          FROM pg_constraint c
          JOIN pg_attribute a
            ON a.attrelid = c.conrelid AND a.attnum = ANY (c.conkey)
         WHERE c.conrelid = format('public.%I', tbl)::regclass
           AND c.contype = 'p'
         GROUP BY c.conname;
        IF pk_name IS NOT NULL
           AND pk_columns <> ARRAY['id']::text[]
           AND EXISTS (
               SELECT 1 FROM information_schema.columns
                WHERE table_schema = 'public' AND table_name = tbl AND column_name = 'id'
           ) THEN
            EXECUTE format('ALTER TABLE public.%I DROP CONSTRAINT %I', tbl, pk_name);
            EXECUTE format('ALTER TABLE public.%I ADD PRIMARY KEY (id)', tbl);
        END IF;
        pk_name := NULL;
        pk_columns := NULL;
    END LOOP;
END
$catalog_copilot_primary_keys$;

CREATE UNIQUE INDEX IF NOT EXISTS data_relationships_scoped_key
    ON data_relationships (workspace_id, from_dataset, from_column, to_dataset, to_column)
 WHERE workspace_id IS NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS data_relationships_legacy_key
    ON data_relationships (from_dataset, from_column, to_dataset, to_column)
 WHERE workspace_id IS NULL;

-- ---------------------------------------------------------------------------
-- data_relationships: origin, lifecycle and cardinality
-- ---------------------------------------------------------------------------
ALTER TABLE data_relationships
    ADD COLUMN IF NOT EXISTS origin TEXT NOT NULL DEFAULT 'manual',
    ADD COLUMN IF NOT EXISTS status TEXT NOT NULL DEFAULT 'active',
    ADD COLUMN IF NOT EXISTS cardinality TEXT,
    ADD COLUMN IF NOT EXISTS confidence NUMERIC(4,3),
    ADD COLUMN IF NOT EXISTS basis JSONB NOT NULL DEFAULT '{}'::jsonb,
    ADD COLUMN IF NOT EXISTS detected_at TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS rules_version TEXT;

-- join_hint used to carry cardinality tokens (many_to_one ...). Cardinality now
-- has its own column and join_hint is restricted to SQL join types.
UPDATE data_relationships
   SET cardinality = CASE lower(btrim(join_hint))
           WHEN 'many_to_one' THEN 'N:1'
           WHEN 'one_to_many' THEN '1:N'
           WHEN 'one_to_one' THEN '1:1'
           WHEN 'many_to_many' THEN 'N:N'
       END,
       join_hint = NULL
 WHERE join_hint IS NOT NULL
   AND lower(btrim(join_hint)) IN
       ('many_to_one', 'one_to_many', 'one_to_one', 'many_to_many');

UPDATE data_relationships
   SET join_hint = upper(btrim(join_hint))
 WHERE join_hint IS NOT NULL
   AND upper(btrim(join_hint)) IN ('INNER', 'LEFT', 'RIGHT', 'FULL')
   AND join_hint <> upper(btrim(join_hint));

UPDATE data_relationships
   SET join_hint = NULL
 WHERE join_hint IS NOT NULL
   AND upper(btrim(join_hint)) NOT IN ('INNER', 'LEFT', 'RIGHT', 'FULL');

DO $catalog_copilot_relationship_checks$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
         WHERE conrelid = 'public.data_relationships'::regclass
           AND conname = 'data_relationships_origin_check'
    ) THEN
        ALTER TABLE data_relationships
            ADD CONSTRAINT data_relationships_origin_check
            CHECK (origin IN ('manual', 'packaged', 'copilot')) NOT VALID;
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
         WHERE conrelid = 'public.data_relationships'::regclass
           AND conname = 'data_relationships_status_check'
    ) THEN
        ALTER TABLE data_relationships
            ADD CONSTRAINT data_relationships_status_check
            CHECK (status IN ('active', 'rejected', 'retired')) NOT VALID;
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
         WHERE conrelid = 'public.data_relationships'::regclass
           AND conname = 'data_relationships_cardinality_check'
    ) THEN
        ALTER TABLE data_relationships
            ADD CONSTRAINT data_relationships_cardinality_check
            CHECK (cardinality IS NULL OR cardinality IN ('1:1', '1:N', 'N:1', 'N:N'))
            NOT VALID;
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
         WHERE conrelid = 'public.data_relationships'::regclass
           AND conname = 'data_relationships_join_hint_check'
    ) THEN
        ALTER TABLE data_relationships
            ADD CONSTRAINT data_relationships_join_hint_check
            CHECK (join_hint IS NULL OR join_hint IN ('INNER', 'LEFT', 'RIGHT', 'FULL'))
            NOT VALID;
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
         WHERE conrelid = 'public.data_relationships'::regclass
           AND conname = 'data_relationships_confidence_check'
    ) THEN
        ALTER TABLE data_relationships
            ADD CONSTRAINT data_relationships_confidence_check
            CHECK (confidence IS NULL OR (confidence >= 0 AND confidence <= 1))
            NOT VALID;
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
         WHERE conrelid = 'public.data_relationships'::regclass
           AND conname = 'data_relationships_basis_check'
    ) THEN
        ALTER TABLE data_relationships
            ADD CONSTRAINT data_relationships_basis_check
            CHECK (
                jsonb_typeof(basis) = 'object'
                AND NOT (basis ?| ARRAY['values', 'examples', 'sample', 'samples', 'min', 'max'])
            ) NOT VALID;
    END IF;
END
$catalog_copilot_relationship_checks$;

ALTER TABLE data_relationships VALIDATE CONSTRAINT data_relationships_origin_check;
ALTER TABLE data_relationships VALIDATE CONSTRAINT data_relationships_status_check;
ALTER TABLE data_relationships VALIDATE CONSTRAINT data_relationships_cardinality_check;
ALTER TABLE data_relationships VALIDATE CONSTRAINT data_relationships_join_hint_check;
ALTER TABLE data_relationships VALIDATE CONSTRAINT data_relationships_confidence_check;
ALTER TABLE data_relationships VALIDATE CONSTRAINT data_relationships_basis_check;

CREATE INDEX IF NOT EXISTS data_relationships_origin_status_idx
    ON data_relationships (workspace_id, origin, status);

-- ---------------------------------------------------------------------------
-- data_catalog: provenance, semantic type and sensitivity classification
-- ---------------------------------------------------------------------------
ALTER TABLE data_catalog
    ADD COLUMN IF NOT EXISTS description_origin TEXT,
    ADD COLUMN IF NOT EXISTS semantic_type TEXT,
    ADD COLUMN IF NOT EXISTS classifications TEXT[] NOT NULL DEFAULT '{}',
    ADD COLUMN IF NOT EXISTS classification_origin TEXT,
    ADD COLUMN IF NOT EXISTS copilot_evidence JSONB NOT NULL DEFAULT '{}'::jsonb,
    ADD COLUMN IF NOT EXISTS copilot_confidence NUMERIC(4,3),
    ADD COLUMN IF NOT EXISTS copilot_at TIMESTAMPTZ;

-- Text that is still exactly the dataset definition's column_mapping entry
-- shipped with the package: it stays packaged, so a newer package text can
-- refresh it while a person's rewrite (manual) never gets replaced.
UPDATE data_catalog dc
   SET description_origin = 'packaged'
  FROM datasets d
 WHERE dc.description_origin IS NULL
   AND COALESCE(btrim(dc.description), '') <> ''
   AND dc.workspace_id IS NOT NULL
   AND d.workspace_id = dc.workspace_id
   AND d.name = dc.dataset
   AND jsonb_typeof(d.column_mapping) = 'object'
   AND jsonb_typeof(d.column_mapping -> dc.column_name) = 'string'
   AND btrim(d.column_mapping ->> dc.column_name) = btrim(dc.description);

-- Only text that still matches one of the fixed templates of the old semantic
-- enrichment (semantic_build_column_description) is machine-inferred. The old
-- form kept the template tags when a person rewrote the text, so tags alone
-- prove nothing: every other non-empty description is authored and stays
-- authoritative.
UPDATE data_catalog
   SET description_origin = CASE
           WHEN COALESCE(tags, '{}'::text[])
                && ARRAY['auto_described', 'semantic_enrichment']::text[]
            AND (
                btrim(description) LIKE 'Identificador de % usado para relacionar registros del dataset % en la capa %.'
                OR btrim(description) LIKE 'Fecha o marca temporal asociada a % dentro del dataset %; se usa para ordenar, filtrar o auditar cambios.'
                OR btrim(description) LIKE 'Estado operativo de % en %; permite segmentar registros activos, cerrados, bloqueados o pendientes segun el origen.'
                OR btrim(description) LIKE 'Metrica o valor cuantitativo de % en %; se usa para agregaciones, KPIs y analisis operativo.'
                OR btrim(description) LIKE 'Atributo descriptivo de % proveniente de %; aporta contexto de negocio para analisis y busqueda semantica.'
            )
           THEN 'copilot'
           ELSE 'manual'
       END
 WHERE description_origin IS NULL
   AND COALESCE(btrim(description), '') <> '';

DO $catalog_copilot_column_checks$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
         WHERE conrelid = 'public.data_catalog'::regclass
           AND conname = 'data_catalog_description_origin_check'
    ) THEN
        ALTER TABLE data_catalog
            ADD CONSTRAINT data_catalog_description_origin_check
            CHECK (description_origin IS NULL
                   OR description_origin IN ('manual', 'packaged', 'copilot')) NOT VALID;
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
         WHERE conrelid = 'public.data_catalog'::regclass
           AND conname = 'data_catalog_semantic_type_check'
    ) THEN
        ALTER TABLE data_catalog
            ADD CONSTRAINT data_catalog_semantic_type_check
            CHECK (semantic_type IS NULL OR semantic_type IN (
                'identifier', 'text', 'date', 'datetime', 'time', 'money',
                'number', 'integer', 'percent', 'boolean', 'complex'
            )) NOT VALID;
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
         WHERE conrelid = 'public.data_catalog'::regclass
           AND conname = 'data_catalog_classifications_check'
    ) THEN
        ALTER TABLE data_catalog
            ADD CONSTRAINT data_catalog_classifications_check
            CHECK (classifications <@ ARRAY['pii', 'financial', 'confidential']::text[])
            NOT VALID;
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
         WHERE conrelid = 'public.data_catalog'::regclass
           AND conname = 'data_catalog_classification_origin_check'
    ) THEN
        ALTER TABLE data_catalog
            ADD CONSTRAINT data_catalog_classification_origin_check
            CHECK (classification_origin IS NULL
                   OR classification_origin IN ('manual', 'packaged', 'copilot')) NOT VALID;
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
         WHERE conrelid = 'public.data_catalog'::regclass
           AND conname = 'data_catalog_copilot_confidence_check'
    ) THEN
        ALTER TABLE data_catalog
            ADD CONSTRAINT data_catalog_copilot_confidence_check
            CHECK (copilot_confidence IS NULL
                   OR (copilot_confidence >= 0 AND copilot_confidence <= 1)) NOT VALID;
    END IF;
    -- Counts only: a key that could carry sampled values is refused here, not
    -- merely avoided by the service.
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
         WHERE conrelid = 'public.data_catalog'::regclass
           AND conname = 'data_catalog_copilot_evidence_counts_only'
    ) THEN
        ALTER TABLE data_catalog
            ADD CONSTRAINT data_catalog_copilot_evidence_counts_only
            CHECK (
                jsonb_typeof(copilot_evidence) = 'object'
                AND NOT (copilot_evidence
                         ?| ARRAY['values', 'examples', 'sample', 'samples', 'min', 'max'])
                AND octet_length(copilot_evidence::text) <= 4096
            ) NOT VALID;
    END IF;
END
$catalog_copilot_column_checks$;

ALTER TABLE data_catalog VALIDATE CONSTRAINT data_catalog_description_origin_check;
ALTER TABLE data_catalog VALIDATE CONSTRAINT data_catalog_semantic_type_check;
ALTER TABLE data_catalog VALIDATE CONSTRAINT data_catalog_classifications_check;
ALTER TABLE data_catalog VALIDATE CONSTRAINT data_catalog_classification_origin_check;
ALTER TABLE data_catalog VALIDATE CONSTRAINT data_catalog_copilot_confidence_check;
ALTER TABLE data_catalog VALIDATE CONSTRAINT data_catalog_copilot_evidence_counts_only;

-- ---------------------------------------------------------------------------
-- catalog_copilot_state: one row per profiled subject and workspace
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS catalog_copilot_state (
    id              BIGSERIAL PRIMARY KEY,
    tenant_id       UUID NOT NULL,
    workspace_id    UUID NOT NULL,
    subject_kind    TEXT NOT NULL,
    subject         TEXT NOT NULL,
    layer           TEXT,
    cartridge       TEXT,
    -- Publication run, generation, schema digest and rules version: an equal
    -- fingerprint means the subject does not need to be profiled again.
    fingerprint     TEXT NOT NULL,
    rules_version   TEXT NOT NULL,
    status          TEXT NOT NULL,
    display_name    TEXT,
    description     TEXT,
    summary         JSONB NOT NULL DEFAULT '{}'::jsonb,
    error_code      TEXT,
    duration_ms     INTEGER,
    profiled_at     TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    CONSTRAINT catalog_copilot_state_scope_fk
        FOREIGN KEY (tenant_id, workspace_id)
        REFERENCES workspaces(tenant_id, id) ON DELETE CASCADE,
    CONSTRAINT catalog_copilot_state_subject_key
        UNIQUE (workspace_id, subject_kind, subject),
    CONSTRAINT catalog_copilot_state_kind_check
        CHECK (subject_kind IN ('dataset', 'bronze_source')),
    CONSTRAINT catalog_copilot_state_subject_check
        CHECK (length(subject) BETWEEN 1 AND 300),
    CONSTRAINT catalog_copilot_state_status_check
        CHECK (status IN ('ready', 'partial', 'failed')),
    CONSTRAINT catalog_copilot_state_fingerprint_check
        CHECK (length(fingerprint) BETWEEN 1 AND 512),
    CONSTRAINT catalog_copilot_state_display_name_check
        CHECK (display_name IS NULL OR length(display_name) <= 200),
    CONSTRAINT catalog_copilot_state_description_check
        CHECK (description IS NULL OR length(description) <= 600),
    CONSTRAINT catalog_copilot_state_summary_counts_only
        CHECK (
            jsonb_typeof(summary) = 'object'
            AND NOT (summary ?| ARRAY['values', 'examples', 'sample', 'samples', 'min', 'max'])
            AND octet_length(summary::text) <= 65536
        ),
    CONSTRAINT catalog_copilot_state_error_code_check
        CHECK (error_code IS NULL OR error_code ~ '^[a-z0-9_]{1,64}$'),
    CONSTRAINT catalog_copilot_state_duration_check
        CHECK (duration_ms IS NULL OR duration_ms >= 0)
);

CREATE INDEX IF NOT EXISTS catalog_copilot_state_recent_idx
    ON catalog_copilot_state (tenant_id, workspace_id, profiled_at DESC);

ALTER TABLE catalog_copilot_state ENABLE ROW LEVEL SECURITY;
ALTER TABLE catalog_copilot_state FORCE ROW LEVEL SECURITY;

DO $catalog_copilot_state_policies$
DECLARE
    rw_roles TEXT;
BEGIN
    SELECT string_agg(quote_ident(rolname), ', ' ORDER BY rolname)
      INTO rw_roles
      FROM pg_roles
     WHERE rolname IN ('omega_console', 'omega_refinement');

    IF rw_roles IS NOT NULL THEN
        EXECUTE 'DROP POLICY IF EXISTS catalog_copilot_state_workspace_scope'
                ' ON catalog_copilot_state';
        EXECUTE format(
            'CREATE POLICY catalog_copilot_state_workspace_scope'
            ' ON catalog_copilot_state'
            ' FOR ALL TO %s'
            ' USING (omega_rls_workspace_matches(tenant_id, workspace_id))'
            ' WITH CHECK (omega_rls_workspace_matches(tenant_id, workspace_id))',
            rw_roles
        );
    ELSE
        RAISE NOTICE 'catalog_copilot_state: no service role present, policy skipped';
    END IF;

    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'omega_mcp_infra') THEN
        EXECUTE 'DROP POLICY IF EXISTS catalog_copilot_state_mcp_read'
                ' ON catalog_copilot_state';
        EXECUTE 'CREATE POLICY catalog_copilot_state_mcp_read'
                ' ON catalog_copilot_state'
                ' FOR SELECT TO omega_mcp_infra'
                ' USING (omega_rls_workspace_matches(tenant_id, workspace_id))';
    END IF;
END
$catalog_copilot_state_policies$;

REVOKE ALL ON catalog_copilot_state FROM PUBLIC;
REVOKE ALL ON SEQUENCE catalog_copilot_state_id_seq FROM PUBLIC;

DO $catalog_copilot_state_grants$
DECLARE
    role_name TEXT;
BEGIN
    FOREACH role_name IN ARRAY ARRAY['omega_console', 'omega_refinement', 'omega_mcp_infra']
    LOOP
        IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = role_name) THEN
            -- No service deletes profiling state: a stale row is overwritten by
            -- the next profile, and workspace deletion cascades.
            EXECUTE format(
                'REVOKE DELETE, TRUNCATE ON catalog_copilot_state FROM %I',
                role_name
            );
        END IF;
    END LOOP;

    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'omega_refinement') THEN
        GRANT SELECT, INSERT, UPDATE ON catalog_copilot_state TO omega_refinement;
        GRANT USAGE, SELECT ON SEQUENCE catalog_copilot_state_id_seq TO omega_refinement;
    END IF;
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'omega_console') THEN
        REVOKE INSERT, UPDATE ON catalog_copilot_state FROM omega_console;
        GRANT SELECT ON catalog_copilot_state TO omega_console;
    END IF;
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'omega_mcp_infra') THEN
        REVOKE INSERT, UPDATE ON catalog_copilot_state FROM omega_mcp_infra;
        GRANT SELECT ON catalog_copilot_state TO omega_mcp_infra;
    END IF;
END
$catalog_copilot_state_grants$;

INSERT INTO schema_migrations (filename, applied_at)
VALUES ('99zzzzu_catalog_copilot.sql', NOW())
ON CONFLICT (filename) DO NOTHING;
