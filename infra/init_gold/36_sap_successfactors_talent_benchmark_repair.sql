-- Fail-closed repair for legacy materialized benchmark rows.

DO $repair$
DECLARE
    relation_name TEXT := 'public.gold_sap_successfactors_talent_benchmark_internal';
BEGIN
    IF to_regclass(relation_name) IS NULL THEN
        RETURN;
    END IF;
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
        'AND approval_actor_source = ''server'' AND approval_recorded_by_server = TRUE '
        'AND NULLIF(TRIM(approval_evidence_ref), '''') IS NOT NULL '
        'AND NULLIF(TRIM(approval_authorization_ref), '''') IS NOT NULL '
        'AND approval_authorization_verified = TRUE), FALSE)',
        relation_name
    );
END
$repair$;

CREATE TABLE IF NOT EXISTS schema_migrations (
    filename TEXT PRIMARY KEY,
    applied_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

INSERT INTO schema_migrations(filename, applied_at)
VALUES ('gold/36_sap_successfactors_talent_benchmark_repair.sql', NOW())
ON CONFLICT (filename) DO NOTHING;
