-- 99zzzz_sap_successfactors_cycle_autoschedule.sql
--
-- A1: make the SuccessFactors cycle autonomous on any install, not just FEMSA.
--
-- Diagnosis: every piece of the automatic cycle already exists — the
-- entity_scheduler meta-DAG fires any entity_config row with
-- trigger_type='scheduled' + cron, and sap_successfactors_extract_all runs the
-- WHOLE cycle in one run (Bronze extraction -> per-entity Silver refresh ->
-- trigger_successfactors_gold_refresh(target) covering the Gold foundation
-- order INCLUDING employees_anomalies -> dataset_refresh_chain). But nothing
-- schedules extract_all: the cartridge seed leaves every row
-- trigger_type='manual', and 99k/99l flip a handful of per-entity rows only
-- when the hardcoded FEMSA tenant/workspace exist (a no-op everywhere else) —
-- and those point at sap_successfactors_extract, which never reaches Gold.
--
-- Repair: one scheduler-visible marker row that fires the cycle DAG. The
-- entity name is prefixed '__' following the cartridge's existing pseudo-entry
-- convention ('__talent_cpa__'), and get_extract_all_plan skips '__'-prefixed
-- rows so the marker can never leak into an extraction plan as a fake entity.
-- Scope resolves at seed time from the server's own state (never hardcoded):
-- the first ready sap_successfactors installation, else the local bootstrap
-- convention (Default Tenant / Main Workspace). No scope -> no-op, so FEMSA
-- and other production installs are untouched.
--
-- The seeding lives in a function so infra/init_dev/15_local_dev_bootstrap.sql
-- can re-run it AFTER the dev bootstrap creates the workspace and
-- installations (on a fresh install this file runs before any workspace
-- exists, so the direct call below is expected to no-op there).

CREATE OR REPLACE FUNCTION public.seed_sap_successfactors_cycle_schedule()
RETURNS integer
LANGUAGE plpgsql
SET search_path = pg_catalog, public, pg_temp
AS $$
DECLARE
    scope_tenant UUID;
    scope_workspace UUID;
    local_scope BOOLEAN := FALSE;
BEGIN
    SELECT ci.tenant_id, ci.workspace_id
      INTO scope_tenant, scope_workspace
      FROM public.cartridge_installations ci
     WHERE ci.cartridge_id = 'sap_successfactors'
       AND ci.status = 'ready'
     ORDER BY ci.ready_at ASC NULLS LAST, ci.created_at ASC
     LIMIT 1;

    IF scope_tenant IS NULL THEN
        SELECT t.id, w.id
          INTO scope_tenant, scope_workspace
          FROM public.tenants t
          JOIN public.workspaces w ON w.tenant_id = t.id
         WHERE t.name = 'Default Tenant'
           AND w.name = 'Main Workspace'
         LIMIT 1;
    END IF;

    IF scope_tenant IS NULL OR scope_workspace IS NULL THEN
        RETURN 0;
    END IF;

    -- Is the resolved scope the local bootstrap convention? Only then may the
    -- per-entity templates be retargeted below; a production install (FEMSA)
    -- resolves to its own workspace name and keeps its rows untouched.
    SELECT TRUE
      INTO local_scope
      FROM public.tenants t
      JOIN public.workspaces w ON w.tenant_id = t.id
     WHERE t.name = 'Default Tenant'
       AND w.name = 'Main Workspace'
       AND t.id = scope_tenant
       AND w.id = scope_workspace;

    IF COALESCE(local_scope, FALSE) THEN
        -- The cartridge seed leaves the foundation entity templates with a
        -- production connection id (femsa_sf via 99k/99l follow-ups) and no
        -- scope; under a conn_id=default cycle the plan skips every one of
        -- them fail-closed ('scope_mismatch'). Bind them to the local scope
        -- and the 'default' Vault connection so the scheduled cycle can
        -- actually extract. trigger_type stays as seeded: the cycle marker
        -- row is the only scheduler entry point.
        UPDATE public.entity_config ec
           SET connection_id = 'default',
               tenant_id     = scope_tenant,
               workspace_id  = scope_workspace
         WHERE ec.cartridge_id = 'sap_successfactors'
           AND ec.entity IN ('User', 'PerPerson', 'PerPersonal', 'EmpEmployment',
                             'EmpJob', 'FOCompany', 'FODepartment', 'FODivision',
                             'FOLocation', 'FOBusinessUnit', 'FOCostCenter',
                             'FOJobCode', 'FOPayGrade', 'Position')
           AND (ec.connection_id IS DISTINCT FROM 'default'
                OR ec.tenant_id IS DISTINCT FROM scope_tenant
                OR ec.workspace_id IS DISTINCT FROM scope_workspace);
    END IF;

    INSERT INTO public.entity_config
        (cartridge_id, entity, display_name, description, mode, enabled,
         dag_id, trigger_type, cron_expression, connection_id, dag_params,
         tenant_id, workspace_id)
    VALUES
        ('sap_successfactors', '__foundation_cycle__',
         'Ciclo Foundation (auto)',
         'Ciclo autonomo SF: Bronce -> Plata -> Oro foundation + anomalias EC. Disparado por entity_scheduler; el DAG sap_successfactors_extract_all orquesta todo el recorrido.',
         'incremental', TRUE,
         'sap_successfactors_extract_all', 'scheduled', '*/15 * * * *',
         'default', '{"target": "foundation"}'::jsonb,
         scope_tenant, scope_workspace)
    ON CONFLICT (cartridge_id, entity) DO UPDATE
       SET dag_id          = EXCLUDED.dag_id,
           trigger_type    = EXCLUDED.trigger_type,
           cron_expression = EXCLUDED.cron_expression,
           connection_id   = EXCLUDED.connection_id,
           dag_params      = EXCLUDED.dag_params,
           enabled         = TRUE,
           tenant_id       = EXCLUDED.tenant_id,
           workspace_id    = EXCLUDED.workspace_id;
    RETURN 1;
END
$$;

SELECT public.seed_sap_successfactors_cycle_schedule();

INSERT INTO schema_migrations (filename, applied_at)
VALUES ('99zzzz_sap_successfactors_cycle_autoschedule.sql', NOW())
ON CONFLICT (filename) DO NOTHING;
