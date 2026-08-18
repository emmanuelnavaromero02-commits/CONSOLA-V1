-- 99zzzzc_sap_successfactors_cycle_target_all.sql
--
-- E1 (Nine Box come): el ciclo autonomo SF materializa el recorrido COMPLETO
-- — foundation + cascada de talento — no solo la base.
--
-- A1 sembro el marcador '__foundation_cycle__' con target=foundation y A2 lo
-- volvio configuracion por cliente (cartridge_cycle_config.target, default
-- 'foundation'). El DAG sap_successfactors_extract_all ya acepta
-- target=all|foundation|talent y refinement_triggers ya enruta 'all' a
-- SUCCESSFACTORS_GOLD_FOUNDATION_ORDER + SUCCESSFACTORS_GOLD_TALENT_ORDER
-- (25 datasets de talento: cpa_scores, readiness, 9box, retention_risk, ...)
-- mas la plata curada de talento. Nada de eso corre hoy porque el target
-- sembrado es 'foundation': el TalentControlRoom (9-box) nunca recibe datos.
--
-- Esta migracion (forward-only; 99zzzz y 99zzzza quedan selladas):
--   1. Cambia el DEFAULT de cartridge_cycle_config.target a 'all'.
--   2. Promueve a 'all' las filas SF existentes que quedaron en 'foundation'
--      (el bootstrap dev y las configs A2 creadas antes de este cambio).
--      Un target distinto ('talent', o un futuro explicito) se respeta.
--   3. Reemplaza el reconciliador para que el self-seed dev tambien nazca
--      con 'all' y la descripcion del marcador diga la verdad del recorrido.
--   4. Re-ejecuta el reconciliador para propagar dag_params al marcador.
--
-- Fail-closed intacto: la cadena de talento no fabrica proxies — si el tenant
-- no trae desempeno/competencia/aspiracion observados, cpa_scores conserva
-- insufficient_data y el 9-box muestra sus bloqueos, no datos inventados.

ALTER TABLE public.cartridge_cycle_config
    ALTER COLUMN target SET DEFAULT 'all';

UPDATE public.cartridge_cycle_config
   SET target = 'all',
       updated_at = NOW()
 WHERE cartridge_id = 'sap_successfactors'
   AND target = 'foundation';

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
        -- self-seed a dev row; a production install seeds NOTHING here.
        -- E1: the dev cycle is born running the FULL journey (target 'all').
        INSERT INTO public.cartridge_cycle_config
            (tenant_id, workspace_id, cartridge_id, connection_id,
             cron_expression, target, enabled)
        SELECT t.id, w.id, 'sap_successfactors', 'default',
               '*/15 * * * *', 'all', TRUE
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
         'Ciclo autonomo SF: Bronce -> Plata -> Oro foundation + talento '
             || '(cascada WB-TALENTO: cpa, readiness, 9box) + anomalias EC. '
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
VALUES ('99zzzzc_sap_successfactors_cycle_target_all.sql', NOW())
ON CONFLICT (filename) DO NOTHING;
