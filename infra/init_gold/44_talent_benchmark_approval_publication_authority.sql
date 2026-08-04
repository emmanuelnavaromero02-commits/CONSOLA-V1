-- Keep the Talent benchmark approval rule durable under staged publication.
--
-- infra/init_gold/38 installs a CHECK on the fixed legacy name
-- public.gold_sap_successfactors_talent_benchmark_internal, and infra/init_gold/37
-- repairs the legacy readiness/9box projections on those same fixed names. Both
-- become silent no-ops once infra/init_gold/40 relocates unscoped public.gold_*
-- into omega_publication_legacy, which is exactly what happens on an already
-- migrated database: the upgrade runner skips filenames already recorded in
-- schema_migrations, so 37 and 38 always execute after 39..43 there.
--
-- This migration resolves the physical relation through
-- omega_publication.dataset_gold_relations instead of a fixed name, repairs the
-- historical rows wherever they live, and installs a deferred constraint trigger
-- so every publication and republication re-imposes the benchmark rule.

-- Gold carries benchmark inputs, never approval authority.  Approval is
-- overlaid at read time from the scoped Console ledger for this exact head.
-- Therefore every published row must remain explicitly unreviewed, regardless
-- of how complete its self-asserted approval_* shape looks.
CREATE OR REPLACE FUNCTION omega_publication.talent_approval_predicate()
RETURNS text
LANGUAGE sql IMMUTABLE
AS $predicate$
  SELECT $$COALESCE((approved IS NOT TRUE
    AND approved_by IS NULL
    AND approved_at IS NULL
    AND approval_actor_source IS NULL
    AND approval_recorded_by_server IS NOT TRUE
    AND approval_evidence_ref IS NULL
    AND approval_authorization_ref IS NULL
    AND approval_authorization_verified IS NOT TRUE
    AND approval_status='unreviewed'), FALSE)$$;
$predicate$;

ALTER FUNCTION omega_publication.talent_approval_predicate() OWNER TO omega_gold_owner;

CREATE OR REPLACE FUNCTION omega_publication.talent_approval_repair(
  p_schema text, p_relation text, p_dataset text
) RETURNS void
LANGUAGE plpgsql
SET search_path = pg_catalog, public
AS $repair$
DECLARE
  target text := format('%I.%I', p_schema, p_relation);
BEGIN
  IF to_regclass(target) IS NULL THEN
    RETURN;
  END IF;

  IF p_dataset = 'sap_successfactors_talent_benchmark_internal' THEN
    EXECUTE format('ALTER TABLE %s ADD COLUMN IF NOT EXISTS approval_source TEXT', target);
    EXECUTE format('ALTER TABLE %s ADD COLUMN IF NOT EXISTS benchmark_version TEXT', target);
    EXECUTE format('ALTER TABLE %s ADD COLUMN IF NOT EXISTS blockers TEXT', target);
    EXECUTE format('ALTER TABLE %s ADD COLUMN IF NOT EXISTS approval_actor_source TEXT', target);
    EXECUTE format('ALTER TABLE %s ADD COLUMN IF NOT EXISTS approval_recorded_by_server BOOLEAN NOT NULL DEFAULT FALSE', target);
    EXECUTE format('ALTER TABLE %s ADD COLUMN IF NOT EXISTS approval_evidence_ref TEXT', target);
    EXECUTE format('ALTER TABLE %s ADD COLUMN IF NOT EXISTS approval_authorization_ref TEXT', target);
    EXECUTE format('ALTER TABLE %s ADD COLUMN IF NOT EXISTS approval_authorization_verified BOOLEAN NOT NULL DEFAULT FALSE', target);
    EXECUTE format('ALTER TABLE %s ADD COLUMN IF NOT EXISTS approval_status TEXT NOT NULL DEFAULT ''unreviewed''', target);
    EXECUTE format(
      'UPDATE %s SET approved=FALSE, approved_by=NULL, approved_at=NULL, '
      'approval_source=''system_default'', approval_actor_source=NULL, '
      'approval_recorded_by_server=FALSE, approval_evidence_ref=NULL, '
      'approval_authorization_ref=NULL, approval_authorization_verified=FALSE, '
      'approval_status=''unreviewed'', '
      'benchmark_version=''talent_benchmark_internal.v1.unreviewed'', '
      'blockers=''["benchmark_internal_unreviewed"]'' '
      'WHERE NOT (%s)', target, omega_publication.talent_approval_predicate()
    );

  ELSIF p_dataset = 'sap_successfactors_talent_readiness' THEN
    EXECUTE format('ALTER TABLE %s ADD COLUMN IF NOT EXISTS benchmark_approval_valid BOOLEAN NOT NULL DEFAULT FALSE', target);
    EXECUTE format('ALTER TABLE %s ADD COLUMN IF NOT EXISTS benchmark_provenance_status TEXT NOT NULL DEFAULT ''stale_unapproved_benchmark''', target);
    EXECUTE format(
      'UPDATE %s SET benchmark_approval_valid=FALSE, '
      'benchmark_provenance_status=''stale_unapproved_benchmark'', '
      'source_mode=''insufficient_data'', readiness_status=''insufficient_data'', '
      'readiness_label=''Datos insuficientes'', benchmark_raw_score=NULL, '
      'benchmark_score=NULL, readiness_score=NULL, confidence=NULL '
      'WHERE source_mode=''benchmark_internal'' '
      'AND NOT (benchmark_approval_valid=TRUE '
      'AND benchmark_provenance_status=''approved_durable'')', target
    );
    EXECUTE format(
      'UPDATE %s SET benchmark_provenance_status=''not_applicable'' '
      'WHERE source_mode=''cpa_real''', target
    );

  ELSIF p_dataset = 'sap_successfactors_talent_9box' THEN
    EXECUTE format('ALTER TABLE %s ADD COLUMN IF NOT EXISTS benchmark_approval_valid BOOLEAN NOT NULL DEFAULT FALSE', target);
    EXECUTE format('ALTER TABLE %s ADD COLUMN IF NOT EXISTS benchmark_provenance_status TEXT NOT NULL DEFAULT ''stale_unapproved_benchmark''', target);
    EXECUTE format(
      'UPDATE %s SET benchmark_approval_valid=FALSE, '
      'benchmark_provenance_status=''stale_unapproved_benchmark'', '
      'source_mode=''insufficient_data'', box_status=''blocked'', '
      'benchmark_performance_proxy=NULL, benchmark_potential_proxy=NULL, '
      'performance_proxy_score=NULL, potential_proxy_score=NULL, '
      'performance_band=NULL, potential_band=NULL '
      'WHERE source_mode=''benchmark_internal'' '
      'AND NOT (benchmark_approval_valid=TRUE '
      'AND benchmark_provenance_status=''approved_durable'')', target
    );
  END IF;
END
$repair$;

ALTER FUNCTION omega_publication.talent_approval_repair(text, text, text)
  OWNER TO omega_gold_owner;
REVOKE ALL ON FUNCTION omega_publication.talent_approval_repair(text, text, text)
  FROM PUBLIC, omega_refinement_gold, omega_gold_publisher, omega_gold_verifier;

-- Install (or reinstall) the CHECK before rows land.  No Gold row can mint
-- authority; even a syntactically complete approval claim fails here.
CREATE OR REPLACE FUNCTION omega_publication.talent_approval_constraint(
  p_schema text, p_relation text
) RETURNS void
LANGUAGE plpgsql
SET search_path = pg_catalog, public
AS $constraint$
DECLARE
  target text := format('%I.%I', p_schema, p_relation);
BEGIN
  IF to_regclass(target) IS NULL THEN
    RETURN;
  END IF;
  IF NOT EXISTS (
    SELECT 1 FROM pg_attribute
     WHERE attrelid = target::regclass AND attname = 'approval_status'
       AND attnum > 0 AND NOT attisdropped
  ) THEN
    RETURN;
  END IF;
  EXECUTE format(
    'ALTER TABLE %s DROP CONSTRAINT IF EXISTS talent_benchmark_approval_authority_check',
    target
  );
  EXECUTE format(
    'ALTER TABLE %s ADD CONSTRAINT talent_benchmark_approval_authority_check '
    'CHECK (%s) NOT VALID',
    target, omega_publication.talent_approval_predicate()
  );
  EXECUTE format(
    'ALTER TABLE %s VALIDATE CONSTRAINT talent_benchmark_approval_authority_check',
    target
  );
END
$constraint$;

ALTER FUNCTION omega_publication.talent_approval_constraint(text, text)
  OWNER TO omega_gold_owner;
REVOKE ALL ON FUNCTION omega_publication.talent_approval_constraint(text, text)
  FROM PUBLIC, omega_refinement_gold, omega_gold_publisher, omega_gold_verifier;

-- Two installers keep the CHECK ahead of every row.
--
-- (a) A DDL event trigger fires the moment publish_materialization creates a
--     benchmark compatibility relation, so the CHECK exists while the table is
--     still empty and every subsequent row INSERT is validated by PostgreSQL
--     itself. This covers the very first publication of a scope.
-- (b) A plain AFTER row trigger on dataset_gold_relations re-imposes the CHECK
--     and re-validates the whole relation every time a publication or
--     republication touches the mapping. It fires at end of statement, so no
--     deferred events are left pending when a later migration rerun issues
--     ALTER TABLE on dataset_gold_relations (43 does exactly that).
CREATE OR REPLACE FUNCTION omega_publication.enforce_talent_approval_on_publication()
RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, public
AS $enforce$
DECLARE
  target text;
  stray bigint;
BEGIN
  IF NEW.dataset <> 'sap_successfactors_talent_benchmark_internal' THEN
    RETURN NULL;
  END IF;
  target := format('public.%I', NEW.relation_name);
  IF to_regclass(target) IS NULL THEN
    RETURN NULL;
  END IF;

  -- A published compatibility relation may only ever hold rows of its own scope.
  EXECUTE format(
    'SELECT count(*) FROM %s WHERE tenant_id::text <> %L OR workspace_id::text <> %L',
    target, NEW.tenant_id::text, NEW.workspace_id::text
  ) INTO stray;
  IF stray > 0 THEN
    RAISE EXCEPTION 'talent benchmark publication crossed its scope'
      USING ERRCODE = '23514';
  END IF;

  PERFORM omega_publication.talent_approval_constraint('public', NEW.relation_name);
  RETURN NULL;
END
$enforce$;

ALTER FUNCTION omega_publication.enforce_talent_approval_on_publication()
  OWNER TO omega_gold_owner;

DROP TRIGGER IF EXISTS talent_approval_publication_authority
  ON omega_publication.dataset_gold_relations;
CREATE TRIGGER talent_approval_publication_authority
  AFTER INSERT OR UPDATE ON omega_publication.dataset_gold_relations
  FOR EACH ROW
  EXECUTE FUNCTION omega_publication.enforce_talent_approval_on_publication();

CREATE OR REPLACE FUNCTION omega_publication.talent_approval_on_create()
RETURNS event_trigger
LANGUAGE plpgsql
SET search_path = pg_catalog, public
AS $on_create$
DECLARE
  created RECORD;
BEGIN
  FOR created IN
    SELECT objid FROM pg_event_trigger_ddl_commands()
     WHERE command_tag = 'CREATE TABLE'
       AND schema_name = 'public'
       AND object_identity LIKE 'public.gold_sap_successfactors_talent_benchmark%'
  LOOP
    PERFORM omega_publication.talent_approval_constraint(
      'public', (SELECT relname FROM pg_class WHERE oid = created.objid)
    );
  END LOOP;
END
$on_create$;

DROP EVENT TRIGGER IF EXISTS talent_approval_on_create;
CREATE EVENT TRIGGER talent_approval_on_create
  ON ddl_command_end
  WHEN TAG IN ('CREATE TABLE')
  EXECUTE FUNCTION omega_publication.talent_approval_on_create();

-- Backfill: repair the history wherever it survived 40's relocation, then pin the
-- constraint on every benchmark relation already mapped.
DO $backfill$
DECLARE
  mapping RECORD;
  legacy RECORD;
  datasets text[] := ARRAY[
    'sap_successfactors_talent_benchmark_internal',
    'sap_successfactors_talent_readiness',
    'sap_successfactors_talent_9box'
  ];
  dataset_name text;
BEGIN
  FOREACH dataset_name IN ARRAY datasets LOOP
    FOR legacy IN
      SELECT n.nspname AS schema_name, c.relname AS relation_name
        FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
       WHERE c.relkind = 'r'
         AND n.nspname IN ('public', 'omega_publication_legacy')
         AND c.relname = 'gold_' || dataset_name
    LOOP
      PERFORM omega_publication.talent_approval_repair(
        legacy.schema_name, legacy.relation_name, dataset_name
      );
    END LOOP;

    FOR mapping IN
      SELECT relation_name FROM omega_publication.dataset_gold_relations
       WHERE dataset = dataset_name
    LOOP
      PERFORM omega_publication.talent_approval_repair(
        'public', mapping.relation_name, dataset_name
      );
    END LOOP;
  END LOOP;

  FOR legacy IN
    SELECT n.nspname AS schema_name, c.relname AS relation_name
      FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
     WHERE c.relkind = 'r'
       AND n.nspname IN ('public', 'omega_publication_legacy')
       AND c.relname = 'gold_sap_successfactors_talent_benchmark_internal'
  LOOP
    PERFORM omega_publication.talent_approval_constraint(
      legacy.schema_name, legacy.relation_name
    );
  END LOOP;

  FOR mapping IN
    SELECT relation_name FROM omega_publication.dataset_gold_relations
     WHERE dataset = 'sap_successfactors_talent_benchmark_internal'
  LOOP
    PERFORM omega_publication.talent_approval_constraint(
      'public', mapping.relation_name
    );
  END LOOP;
END
$backfill$;

CREATE TABLE IF NOT EXISTS schema_migrations (
    id BIGSERIAL PRIMARY KEY,
    filename TEXT NOT NULL UNIQUE,
    applied_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    checksum TEXT
);

INSERT INTO schema_migrations(filename, applied_at)
VALUES ('gold/44_talent_benchmark_approval_publication_authority.sql', NOW())
ON CONFLICT (filename) DO NOTHING;
