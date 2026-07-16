-- 99zz_market_context_enable_schedule.sql
--
-- Paso 1 (ruta crítica market-context): habilita el refresh AUTOMÁTICO de los
-- cartridges de mercado externo (Banxico, INEGI, SEC EDGAR) para el scope
-- autorizado "fc america / fc america".
--
-- Antes: su entity_config era trigger_type='manual' con cron NULL, así que el
-- entity_scheduler nunca los disparaba → el Bronze no se re-extraía y el Gold
-- market_context NUNCA se materializaba (readiness clavado en insufficient_data;
-- verificado en prod: las tablas gold no existen).
--
-- Patrón: copia 99l_sap_successfactors_gold_foundation_schedule.sql —
--   * scope FEMSA guardado por EXISTS (installs genéricos/locales quedan manual),
--   * cron por fuente dimensionado a la cadencia real de publicación y a los
--     rate-limiters client-side (Banxico 20/min; SEC 10/s),
--   * los extract DAGs son Bronze-only, así que además se agenda un
--     dataset_refresh_chain por fuente que propaga Bronze → Silver → Gold
--     poco después de cada extract.
-- Reversible: revertir trigger_type a 'manual' apaga el cron al instante.

-- 1a. Bronze auto-extract: scheduled + cron por fuente, fc america-scoped.
WITH market_scope AS (
    SELECT
        'd5d95d5e-0326-4f36-b04f-2a3b77ed61d2'::uuid AS tenant_id,
        '4a6e9743-d54e-46ff-a023-111f06572c42'::uuid AS workspace_id
    WHERE EXISTS (SELECT 1 FROM tenants     WHERE id = 'd5d95d5e-0326-4f36-b04f-2a3b77ed61d2'::uuid)
      AND EXISTS (SELECT 1 FROM workspaces  WHERE id = '4a6e9743-d54e-46ff-a023-111f06572c42'::uuid)
),
desired_extract(cartridge_id, entity, cron_expression) AS (
    VALUES
        ('banxico',   'series_observations', '0 13 * * 1-5'),  -- días hábiles ~13:00 (tras el fix FIX)
        ('inegi',     'series_observations', '0 14 * * 1'),    -- semanal (INEGI publica mensual)
        ('sec_edgar', 'company_facts',       '0 15 * * 1')     -- semanal (filings infrecuentes)
)
UPDATE entity_config ec
   SET trigger_type    = 'scheduled',
       cron_expression = d.cron_expression,
       tenant_id       = fs.tenant_id,
       workspace_id    = fs.workspace_id
  FROM desired_extract d, market_scope fs
 WHERE ec.cartridge_id = d.cartridge_id
   AND ec.entity       = d.entity;

-- 1b. Gold auto-refresh: dataset_refresh_chain por fuente (Bronze → Silver → Gold),
-- ~30 min después del extract. seed_raw = el dataset raw que el extract refresca;
-- el chain propaga downstream por datasets.sources hasta el gold market_context.
-- Scope FEMSA guardado por WHERE EXISTS (installs sin ese scope no insertan nada).
INSERT INTO entity_config
    (cartridge_id, entity, display_name, mode, dag_id, enabled,
     trigger_type, cron_expression, tenant_id, workspace_id, dag_params)
SELECT v.cartridge_id, v.entity, v.display_name, 'full', 'dataset_refresh_chain', TRUE,
       'scheduled', v.cron_expression,
       'd5d95d5e-0326-4f36-b04f-2a3b77ed61d2'::uuid,
       '4a6e9743-d54e-46ff-a023-111f06572c42'::uuid,
       jsonb_build_object('seed_raw', v.seed_raw)
  FROM (VALUES
        ('banxico',   'market_context_refresh', 'Banxico market context refresh',   '30 13 * * 1-5', 'raw/banxico/series_observations'),
        ('inegi',     'market_context_refresh', 'INEGI market context refresh',      '30 14 * * 1',   'raw/inegi/series_observations'),
        ('sec_edgar', 'market_context_refresh', 'SEC EDGAR market context refresh',  '30 15 * * 1',   'raw/sec_edgar/company_facts')
       ) AS v(cartridge_id, entity, display_name, cron_expression, seed_raw)
 WHERE EXISTS (SELECT 1 FROM tenants    WHERE id = 'd5d95d5e-0326-4f36-b04f-2a3b77ed61d2'::uuid)
   AND EXISTS (SELECT 1 FROM workspaces WHERE id = '4a6e9743-d54e-46ff-a023-111f06572c42'::uuid)
ON CONFLICT (cartridge_id, entity) DO UPDATE
   SET dag_id          = EXCLUDED.dag_id,
       enabled         = EXCLUDED.enabled,
       trigger_type    = EXCLUDED.trigger_type,
       cron_expression = EXCLUDED.cron_expression,
       tenant_id       = EXCLUDED.tenant_id,
       workspace_id    = EXCLUDED.workspace_id,
       dag_params      = EXCLUDED.dag_params;

INSERT INTO schema_migrations (filename, applied_at)
VALUES ('99zz_market_context_enable_schedule.sql', NOW())
ON CONFLICT (filename) DO NOTHING;
