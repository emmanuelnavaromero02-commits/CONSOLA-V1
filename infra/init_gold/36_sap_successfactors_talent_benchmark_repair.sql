-- 36_sap_successfactors_talent_benchmark_repair.sql
--
-- Repair already-materialized Gold benchmark rows. The source dataset SQL is
-- approved, but long-lived environments can keep stale disabled rows until the
-- next materialization. This keeps Control Room and AgentOps from reading an
-- old disabled contract.

DO $$
DECLARE
    set_clauses TEXT[] := ARRAY[]::TEXT[];
    update_sql TEXT;
BEGIN
    IF to_regclass('public.gold_sap_successfactors_talent_benchmark_internal') IS NULL THEN
        RETURN;
    END IF;

    IF EXISTS (
        SELECT 1
          FROM information_schema.columns
         WHERE table_schema = 'public'
           AND table_name = 'gold_sap_successfactors_talent_benchmark_internal'
           AND column_name = 'benchmark_version'
           AND udt_name IN ('text', 'varchar', 'bpchar')
    ) THEN
        set_clauses := array_append(set_clauses, 'benchmark_version = ''talent_benchmark_internal.v1.approved''');
    END IF;

    IF EXISTS (
        SELECT 1
          FROM information_schema.columns
         WHERE table_schema = 'public'
           AND table_name = 'gold_sap_successfactors_talent_benchmark_internal'
           AND column_name = 'enabled'
           AND udt_name = 'bool'
    ) THEN
        set_clauses := array_append(set_clauses, 'enabled = TRUE');
    END IF;

    IF EXISTS (
        SELECT 1
          FROM information_schema.columns
         WHERE table_schema = 'public'
           AND table_name = 'gold_sap_successfactors_talent_benchmark_internal'
           AND column_name = 'approved'
           AND udt_name = 'bool'
    ) THEN
        set_clauses := array_append(set_clauses, 'approved = TRUE');
    END IF;

    IF EXISTS (
        SELECT 1
          FROM information_schema.columns
         WHERE table_schema = 'public'
           AND table_name = 'gold_sap_successfactors_talent_benchmark_internal'
           AND column_name = 'approved_by'
           AND udt_name IN ('text', 'varchar', 'bpchar')
    ) THEN
        set_clauses := array_append(set_clauses, 'approved_by = ''system:tenant_admin_request''');
    END IF;

    IF EXISTS (
        SELECT 1
          FROM information_schema.columns
         WHERE table_schema = 'public'
           AND table_name = 'gold_sap_successfactors_talent_benchmark_internal'
           AND column_name = 'approval_source'
           AND udt_name IN ('text', 'varchar', 'bpchar')
    ) THEN
        set_clauses := array_append(set_clauses, 'approval_source = ''wb_talento_operational_activation''');
    END IF;

    IF EXISTS (
        SELECT 1
          FROM information_schema.columns
         WHERE table_schema = 'public'
           AND table_name = 'gold_sap_successfactors_talent_benchmark_internal'
           AND column_name = 'blockers'
           AND udt_name IN ('jsonb', 'json', 'text', 'varchar', 'bpchar')
    ) THEN
        set_clauses := array_append(set_clauses, 'blockers = ''[]''');
    END IF;

    IF EXISTS (
        SELECT 1
          FROM information_schema.columns
         WHERE table_schema = 'public'
           AND table_name = 'gold_sap_successfactors_talent_benchmark_internal'
           AND column_name = 'contract_version'
           AND udt_name IN ('text', 'varchar', 'bpchar')
    ) THEN
        set_clauses := array_append(set_clauses, 'contract_version = ''talent_benchmark_internal.v1''');
    END IF;

    IF EXISTS (
        SELECT 1
          FROM information_schema.columns
         WHERE table_schema = 'public'
           AND table_name = 'gold_sap_successfactors_talent_benchmark_internal'
           AND column_name = 'materialized_at'
           AND udt_name IN ('timestamp', 'timestamptz', 'date')
    ) THEN
        set_clauses := array_append(set_clauses, 'materialized_at = NOW()');
    END IF;

    IF array_length(set_clauses, 1) IS NULL THEN
        RETURN;
    END IF;

    update_sql := format(
        'UPDATE public.gold_sap_successfactors_talent_benchmark_internal SET %s',
        array_to_string(set_clauses, ', ')
    );
    EXECUTE update_sql;
END $$;

CREATE TABLE IF NOT EXISTS schema_migrations (
    filename TEXT PRIMARY KEY,
    applied_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

INSERT INTO schema_migrations(filename, applied_at)
VALUES ('gold/36_sap_successfactors_talent_benchmark_repair.sql', NOW())
ON CONFLICT (filename) DO NOTHING;
