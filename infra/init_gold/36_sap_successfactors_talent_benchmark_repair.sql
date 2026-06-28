-- 36_sap_successfactors_talent_benchmark_repair.sql
--
-- Repair already-materialized Gold benchmark rows. The source dataset SQL is
-- approved, but long-lived environments can keep stale disabled rows until the
-- next materialization. This keeps Control Room and AgentOps from reading an
-- old disabled contract.

DO $$
DECLARE
    has_required_columns BOOLEAN;
BEGIN
    IF to_regclass('public.gold_sap_successfactors_talent_benchmark_internal') IS NULL THEN
        RETURN;
    END IF;

    SELECT COUNT(*) = 8
      INTO has_required_columns
      FROM information_schema.columns
     WHERE table_schema = 'public'
       AND table_name = 'gold_sap_successfactors_talent_benchmark_internal'
       AND column_name IN (
           'benchmark_version',
           'enabled',
           'approved',
           'approved_by',
           'approval_source',
           'blockers',
           'contract_version',
           'materialized_at'
       );

    IF NOT has_required_columns THEN
        RETURN;
    END IF;

    EXECUTE $sql$
        UPDATE public.gold_sap_successfactors_talent_benchmark_internal
           SET benchmark_version = 'talent_benchmark_internal.v1.approved',
               enabled = TRUE,
               approved = TRUE,
               approved_by = 'system:tenant_admin_request',
               approval_source = 'wb_talento_operational_activation',
               blockers = '[]',
               contract_version = 'talent_benchmark_internal.v1',
               materialized_at = NOW()
    $sql$;
END $$;

INSERT INTO schema_migrations(filename, applied_at)
VALUES ('gold/36_sap_successfactors_talent_benchmark_repair.sql', NOW())
ON CONFLICT (filename) DO NOTHING;
