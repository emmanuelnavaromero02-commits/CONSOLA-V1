-- 99zzzza_sap_successfactors_cycle_config.sql
--
-- A2: the autonomous SF cycle is driven by a per-client configuration —
-- real connection, real cadence — and a production install never seeds an
-- empty cycle again.
--
-- A1's seed_sap_successfactors_cycle_schedule() hardcoded
-- connection_id='default' and cron '*/15 * * * *' and activated the cycle on
-- ANY install with a ready SF installation: on a real client that schedules
-- a quarter-hourly run that extracts nothing (fail-closed against a
-- connection the client never configured). This migration adds the managed
-- source of truth and rewrites the seeder as its reconciler:
--
--   cartridge_cycle_config(tenant_id, workspace_id, cartridge_id,
--       connection_id, cron_expression, target, enabled, ...)
--
-- Paso 0 (surveyed before adding this): the Console has no per-workspace
-- designated-connection registry to reuse — cartridge_connections is a
-- GLOBAL catalog of connection definitions (no scope, no primary flag) and
-- vault_entries stores the credentials themselves. This table is the
-- missing binding.
--
-- Invariants the reconciler enforces:
--   * ONE active SF cycle at a time (partial unique index): entity_config's
--     PRIMARY KEY (cartridge_id, entity) is global, so a single marker row —
--     and a single bound foundation-template set — can exist. Concurrent
--     multi-client cycles need the entity_config multi-tenant refactor (A3).
--   * A row whose credential is absent from vault_entries seeds the cycle
--     marker DISABLED ("configured, waiting for credential") and touches
--     nothing else — never an empty scheduled run.
--   * The dev convention (Default Tenant / Main Workspace + connection
--     'default') keeps A1's local behaviour byte-for-byte: it is seeded by
--     the bootstrap, and it skips the credential gate because on a fresh
--     local install the bootstrap runs before any credential can exist.
--   * Only the 14 foundation entity templates are ever rebound; every other
--     entity_config row (e.g. FEMSA's per-entity scheduled rows such as
--     PerEmail / PaymentInformationDetailV3 / EmpEmploymentTermination)
--     stays untouched.
--
-- The reconciler runs at migration/bootstrap time (superuser) or when an
-- operator invokes SELECT public.seed_sap_successfactors_cycle_schedule()
-- after changing the config or saving the credential. Re-running it from
-- the Console's connection-save path is a follow-up (it would touch the
-- console vault surface, adjacent to what F-SEG hardened).
--
-- Naming debt (for the owner): the 99z* prefix space is exhausted enough
-- that this file needs a 99zzzza prefix to sort strictly after
-- 99zzzz_sap_successfactors_cycle_autoschedule.sql. The init ordering
-- scheme needs a real sequence (e.g. numbered 3-digit prefixes) before the
-- next migration lands.

CREATE TABLE IF NOT EXISTS public.cartridge_cycle_config (
    tenant_id       UUID NOT NULL REFERENCES public.tenants(id) ON DELETE CASCADE,
    workspace_id    UUID NOT NULL REFERENCES public.workspaces(id) ON DELETE CASCADE,
    cartridge_id    TEXT NOT NULL,
    connection_id   TEXT NOT NULL,
    cron_expression TEXT NOT NULL DEFAULT '*/15 * * * *',
    target          TEXT NOT NULL DEFAULT 'foundation',
    enabled         BOOLEAN NOT NULL DEFAULT TRUE,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (tenant_id, workspace_id, cartridge_id)
);

-- entity_config's global PK admits one bound cycle per cartridge; make the
-- "one active" rule a hard constraint instead of an ordering accident.
CREATE UNIQUE INDEX IF NOT EXISTS cartridge_cycle_config_single_active
    ON public.cartridge_cycle_config (cartridge_id)
    WHERE enabled;

-- Tenant-scoped RLS, same boundary shape the rest of the schema uses. The
-- reconciler itself runs with the migration/bootstrap superuser and is not
-- affected; the Console manages config rows only inside its own workspace.
ALTER TABLE public.cartridge_cycle_config ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.cartridge_cycle_config FORCE ROW LEVEL SECURITY;
REVOKE ALL ON public.cartridge_cycle_config FROM PUBLIC;
GRANT SELECT, INSERT, UPDATE, DELETE ON public.cartridge_cycle_config TO omega_console;
DROP POLICY IF EXISTS cartridge_cycle_config_tenant_workspace_rls
    ON public.cartridge_cycle_config;
CREATE POLICY cartridge_cycle_config_tenant_workspace_rls
    ON public.cartridge_cycle_config
    FOR ALL TO omega_console
    USING (public.omega_rls_workspace_matches(tenant_id, workspace_id))
    WITH CHECK (public.omega_rls_workspace_matches(tenant_id, workspace_id));

CREATE OR REPLACE FUNCTION public.seed_sap_successfactors_cycle_schedule()
RETURNS integer
LANGUAGE plpgsql
SET search_path = pg_catalog, public, pg_temp
AS $$
DECLARE
    cfg RECORD;
    dev_scope BOOLEAN := FALSE;
    has_credential BOOLEAN := FALSE;
BEGIN
    IF to_regclass('public.cartridge_cycle_config') IS NULL THEN
        RETURN 0;
    END IF;

    SELECT c.* INTO cfg
      FROM public.cartridge_cycle_config c
     WHERE c.cartridge_id = 'sap_successfactors'
       AND c.enabled
     LIMIT 1;

    IF cfg.tenant_id IS NULL THEN
        -- No managed cycle. Only the local bootstrap convention may
        -- self-seed a dev row; a production install seeds NOTHING here
        -- (A1's "any ready installation -> default cycle" is gone).
        INSERT INTO public.cartridge_cycle_config
            (tenant_id, workspace_id, cartridge_id, connection_id,
             cron_expression, target, enabled)
        SELECT t.id, w.id, 'sap_successfactors', 'default',
               '*/15 * * * *', 'foundation', TRUE
          FROM public.tenants t
          JOIN public.workspaces w ON w.tenant_id = t.id
         WHERE t.name = 'Default Tenant'
           AND w.name = 'Main Workspace'
         LIMIT 1
        ON CONFLICT (tenant_id, workspace_id, cartridge_id) DO NOTHING;

        SELECT c.* INTO cfg
          FROM public.cartridge_cycle_config c
         WHERE c.cartridge_id = 'sap_successfactors'
           AND c.enabled
         LIMIT 1;
        IF cfg.tenant_id IS NULL THEN
            RETURN 0;
        END IF;
    END IF;

    SELECT TRUE INTO dev_scope
      FROM public.tenants t
      JOIN public.workspaces w ON w.tenant_id = t.id
     WHERE t.id = cfg.tenant_id
       AND w.id = cfg.workspace_id
       AND t.name = 'Default Tenant'
       AND w.name = 'Main Workspace'
       AND cfg.connection_id = 'default';

    has_credential := EXISTS (
        SELECT 1
          FROM public.vault_entries v
         WHERE v.scope = 'connections'
           AND v.cartridge = 'sap_successfactors'
           AND v.key = cfg.connection_id
           AND v.tenant_id = cfg.tenant_id
           AND v.workspace_id = cfg.workspace_id
    );

    IF NOT has_credential AND NOT COALESCE(dev_scope, FALSE) THEN
        -- Configured, waiting for the credential: the marker stays visible
        -- but DISABLED, the templates stay unbound, and the scheduler
        -- (enabled = TRUE filter) never fires an empty run.
        INSERT INTO public.entity_config
            (cartridge_id, entity, display_name, description, mode, enabled,
             dag_id, trigger_type, cron_expression, connection_id, dag_params,
             tenant_id, workspace_id)
        VALUES
            ('sap_successfactors', '__foundation_cycle__',
             'Ciclo Foundation (auto)',
             'Configurado, esperando credencial: vault sin conexion "'
                 || cfg.connection_id || '" para el workspace del ciclo. '
                 || 'Guarda la conexion y re-ejecuta el reconciliador.',
             'incremental', FALSE,
             'sap_successfactors_extract_all', 'scheduled',
             cfg.cron_expression, cfg.connection_id,
             jsonb_build_object('target', cfg.target),
             cfg.tenant_id, cfg.workspace_id)
        ON CONFLICT (cartridge_id, entity) DO UPDATE
           SET display_name    = EXCLUDED.display_name,
               description     = EXCLUDED.description,
               dag_id          = EXCLUDED.dag_id,
               trigger_type    = EXCLUDED.trigger_type,
               cron_expression = EXCLUDED.cron_expression,
               connection_id   = EXCLUDED.connection_id,
               dag_params      = EXCLUDED.dag_params,
               enabled         = FALSE,
               tenant_id       = EXCLUDED.tenant_id,
               workspace_id    = EXCLUDED.workspace_id;
        RETURN 2;
    END IF;

    -- Bind the 14 foundation entity templates to the ACTIVE config. Nothing
    -- outside this list is touched: other scheduled rows (FEMSA's PerEmail,
    -- PaymentInformationDetailV3, EmpEmploymentTermination, ...) keep their
    -- connection and scope.
    UPDATE public.entity_config ec
       SET connection_id = cfg.connection_id,
           tenant_id     = cfg.tenant_id,
           workspace_id  = cfg.workspace_id
     WHERE ec.cartridge_id = 'sap_successfactors'
       AND ec.entity IN ('User', 'PerPerson', 'PerPersonal', 'EmpEmployment',
                         'EmpJob', 'FOCompany', 'FODepartment', 'FODivision',
                         'FOLocation', 'FOBusinessUnit', 'FOCostCenter',
                         'FOJobCode', 'FOPayGrade', 'Position')
       AND (ec.connection_id IS DISTINCT FROM cfg.connection_id
            OR ec.tenant_id IS DISTINCT FROM cfg.tenant_id
            OR ec.workspace_id IS DISTINCT FROM cfg.workspace_id);

    INSERT INTO public.entity_config
        (cartridge_id, entity, display_name, description, mode, enabled,
         dag_id, trigger_type, cron_expression, connection_id, dag_params,
         tenant_id, workspace_id)
    VALUES
        ('sap_successfactors', '__foundation_cycle__',
         'Ciclo Foundation (auto)',
         'Ciclo autonomo SF: Bronce -> Plata -> Oro foundation + anomalias EC. '
             || 'Manejado por cartridge_cycle_config; disparado por entity_scheduler; '
             || 'el DAG sap_successfactors_extract_all orquesta todo el recorrido.',
         'incremental', TRUE,
         'sap_successfactors_extract_all', 'scheduled',
         cfg.cron_expression, cfg.connection_id,
         jsonb_build_object('target', cfg.target),
         cfg.tenant_id, cfg.workspace_id)
    ON CONFLICT (cartridge_id, entity) DO UPDATE
       SET display_name    = EXCLUDED.display_name,
           description     = EXCLUDED.description,
           dag_id          = EXCLUDED.dag_id,
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
VALUES ('99zzzza_sap_successfactors_cycle_config.sql', NOW())
ON CONFLICT (filename) DO NOTHING;
