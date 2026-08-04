-- Repair legacy talent projections in the separate modecissions_gold database.
-- The upgrade runner registers this new filename only after the repair succeeds.

DO $repair$
DECLARE
    relation_name TEXT;
BEGIN
    relation_name := 'public.gold_sap_successfactors_talent_benchmark_internal';
    IF to_regclass(relation_name) IS NOT NULL THEN
        EXECUTE format(
            'ALTER TABLE %s ADD COLUMN IF NOT EXISTS approval_actor_source TEXT',
            relation_name
        );
        EXECUTE format(
            'ALTER TABLE %s ADD COLUMN IF NOT EXISTS approval_recorded_by_server BOOLEAN NOT NULL DEFAULT FALSE',
            relation_name
        );
        EXECUTE format(
            'ALTER TABLE %s ADD COLUMN IF NOT EXISTS approval_evidence_ref TEXT',
            relation_name
        );
        EXECUTE format(
            'ALTER TABLE %s ADD COLUMN IF NOT EXISTS approval_authorization_ref TEXT',
            relation_name
        );
        EXECUTE format(
            'ALTER TABLE %s ADD COLUMN IF NOT EXISTS approval_authorization_verified BOOLEAN NOT NULL DEFAULT FALSE',
            relation_name
        );
        EXECUTE format(
            'ALTER TABLE %s ADD COLUMN IF NOT EXISTS approval_status TEXT NOT NULL DEFAULT ''unreviewed''',
            relation_name
        );
        EXECUTE format(
            'UPDATE %s SET approved = FALSE, approved_by = NULL, approved_at = NULL, '
            'approval_source = ''system_default'', approval_actor_source = NULL, '
            'approval_recorded_by_server = FALSE, approval_evidence_ref = NULL, '
            'approval_authorization_ref = NULL, approval_authorization_verified = FALSE, '
            'approval_status = ''unreviewed'', '
            'benchmark_version = ''talent_benchmark_internal.v1.unreviewed'', '
            'blockers = ''["benchmark_internal_unreviewed"]'' '
            'WHERE NOT COALESCE((approved = TRUE '
            'AND TRIM(CAST(approved_by AS TEXT)) ~ ''^[1-9][0-9]*$'' '
            'AND approved_at IS NOT NULL '
            'AND approval_actor_source = ''server'' '
            'AND approval_recorded_by_server = TRUE '
            'AND NULLIF(TRIM(approval_evidence_ref), '''') IS NOT NULL '
            'AND NULLIF(TRIM(approval_authorization_ref), '''') IS NOT NULL '
            'AND approval_authorization_verified = TRUE), FALSE)',
            relation_name
        );
    END IF;

    relation_name := 'public.gold_sap_successfactors_talent_readiness';
    IF to_regclass(relation_name) IS NOT NULL THEN
        EXECUTE format(
            'ALTER TABLE %s ADD COLUMN IF NOT EXISTS benchmark_approval_valid BOOLEAN NOT NULL DEFAULT FALSE',
            relation_name
        );
        EXECUTE format(
            'ALTER TABLE %s ADD COLUMN IF NOT EXISTS benchmark_provenance_status TEXT NOT NULL DEFAULT ''stale_unapproved_benchmark''',
            relation_name
        );
        EXECUTE format(
            'UPDATE %s SET benchmark_approval_valid = FALSE, '
            'benchmark_provenance_status = ''stale_unapproved_benchmark'', '
            'source_mode = ''insufficient_data'', readiness_status = ''insufficient_data'', '
            'readiness_label = ''Datos insuficientes'', benchmark_raw_score = NULL, '
            'benchmark_score = NULL, readiness_score = NULL, confidence = NULL '
            'WHERE source_mode = ''benchmark_internal'' '
            'AND NOT (benchmark_approval_valid = TRUE '
            'AND benchmark_provenance_status = ''approved_durable'')',
            relation_name
        );
        EXECUTE format(
            'UPDATE %s SET benchmark_provenance_status = ''not_applicable'' '
            'WHERE source_mode = ''cpa_real''',
            relation_name
        );
    END IF;

    relation_name := 'public.gold_sap_successfactors_talent_9box';
    IF to_regclass(relation_name) IS NOT NULL THEN
        EXECUTE format(
            'ALTER TABLE %s ADD COLUMN IF NOT EXISTS benchmark_approval_valid BOOLEAN NOT NULL DEFAULT FALSE',
            relation_name
        );
        EXECUTE format(
            'ALTER TABLE %s ADD COLUMN IF NOT EXISTS benchmark_provenance_status TEXT NOT NULL DEFAULT ''stale_unapproved_benchmark''',
            relation_name
        );
        EXECUTE format(
            'UPDATE %s SET benchmark_approval_valid = FALSE, '
            'benchmark_provenance_status = ''stale_unapproved_benchmark'', '
            'source_mode = ''insufficient_data'', box_status = ''blocked'', '
            'benchmark_performance_proxy = NULL, benchmark_potential_proxy = NULL, '
            'performance_proxy_score = NULL, potential_proxy_score = NULL, '
            'performance_band = NULL, potential_band = NULL '
            'WHERE source_mode = ''benchmark_internal'' '
            'AND NOT (benchmark_approval_valid = TRUE '
            'AND benchmark_provenance_status = ''approved_durable'')',
            relation_name
        );
    END IF;
END
$repair$;

CREATE TABLE IF NOT EXISTS schema_migrations (
    id BIGSERIAL PRIMARY KEY,
    filename TEXT NOT NULL UNIQUE,
    applied_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    checksum TEXT
);

INSERT INTO schema_migrations(filename, applied_at)
VALUES ('gold/37_sap_successfactors_talent_operational_truth_repair.sql', NOW())
ON CONFLICT (filename) DO NOTHING;
