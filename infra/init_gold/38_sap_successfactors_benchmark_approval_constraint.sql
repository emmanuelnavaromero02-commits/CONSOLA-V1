-- Prevent a benchmark row from claiming approval without durable authority.

DO $constraint$
DECLARE
    relation_name TEXT := 'public.gold_sap_successfactors_talent_benchmark_internal';
BEGIN
    IF to_regclass(relation_name) IS NULL THEN
        RETURN;
    END IF;
    EXECUTE format(
        'UPDATE %s SET approved=FALSE, approved_by=NULL, approved_at=NULL, '
        'approval_actor_source=NULL, approval_recorded_by_server=FALSE, '
        'approval_evidence_ref=NULL, approval_authorization_ref=NULL, '
        'approval_authorization_verified=FALSE, approval_status=''unreviewed'' '
        'WHERE NOT COALESCE((approved=TRUE AND approved_by IS NOT NULL '
        'AND approved_at IS NOT NULL '
        'AND trim(approved_by::text) ~ ''^[1-9][0-9]*$'' '
        'AND approval_actor_source=''server'' '
        'AND approval_recorded_by_server=TRUE '
        'AND NULLIF(trim(approval_evidence_ref), '''') IS NOT NULL '
        'AND NULLIF(trim(approval_authorization_ref), '''') IS NOT NULL '
        'AND approval_authorization_verified=TRUE '
        'AND approval_status=''approved''), FALSE)',
        relation_name
    );
    EXECUTE format(
        'ALTER TABLE %s DROP CONSTRAINT IF EXISTS '
        'talent_benchmark_approval_authority_check',
        relation_name
    );
    EXECUTE format(
        'ALTER TABLE %s ADD CONSTRAINT talent_benchmark_approval_authority_check '
        'CHECK ((approved IS NOT TRUE AND approval_status=''unreviewed'') OR '
        '(approved=TRUE AND approved_by IS NOT NULL AND approved_at IS NOT NULL '
        'AND trim(approved_by::text) ~ ''^[1-9][0-9]*$'' '
        'AND approval_actor_source=''server'' '
        'AND approval_recorded_by_server=TRUE '
        'AND NULLIF(trim(approval_evidence_ref), '''') IS NOT NULL '
        'AND NULLIF(trim(approval_authorization_ref), '''') IS NOT NULL '
        'AND approval_authorization_verified=TRUE '
        'AND approval_status=''approved'')) NOT VALID',
        relation_name
    );
    EXECUTE format(
        'ALTER TABLE %s VALIDATE CONSTRAINT '
        'talent_benchmark_approval_authority_check',
        relation_name
    );
END
$constraint$;

INSERT INTO schema_migrations(filename, applied_at)
VALUES ('gold/38_sap_successfactors_benchmark_approval_constraint.sql', NOW())
ON CONFLICT (filename) DO NOTHING;
