-- 99l_sap_successfactors_gold_foundation_schedule.sql
--
-- Enables the Foundation Object and termination entities required by the
-- packaged SuccessFactors Gold datasets already shipped with the cartridge.
--
-- The dataset SQL itself is sourced from cartridges/sap_successfactors/datasets
-- and refreshed by Console startup seed_packaged_datasets. This migration keeps
-- the operational catalog reproducible in long-lived AWS volumes:
--   - explicit OData v2 select fields for the live entities,
--   - FEMSA scoped Vault connection propagation,
--   - daily scheduled extraction via entity_scheduler.

INSERT INTO entity_config
    (cartridge_id, entity, odata_entity, display_name, description, mode,
     watermark_field, page_size, primary_key, dag_id, enabled, trigger_type,
     select_fields, effective_dated, date_field)
VALUES
    ('sap_successfactors', 'FOCompany', 'FOCompany', 'Compania',
     'Objeto de fundacion: companias', 'full', NULL, 500, 'externalCode',
     'sap_successfactors_extract', TRUE, 'manual',
     '["externalCode","name_defaultValue","country","status","startDate","endDate","lastModifiedDateTime"]'::jsonb,
     FALSE, NULL),
    ('sap_successfactors', 'FODepartment', 'FODepartment', 'Departamento',
     'Objeto de fundacion: departamentos', 'full', NULL, 1000, 'externalCode',
     'sap_successfactors_extract', TRUE, 'manual',
     '["externalCode","name_defaultValue","costCenter","status","startDate","endDate","lastModifiedDateTime"]'::jsonb,
     FALSE, NULL),
    ('sap_successfactors', 'FODivision', 'FODivision', 'Division',
     'Objeto de fundacion: divisiones', 'full', NULL, 500, 'externalCode',
     'sap_successfactors_extract', TRUE, 'manual',
     '["externalCode","name_defaultValue","status","startDate","endDate","lastModifiedDateTime"]'::jsonb,
     FALSE, NULL),
    ('sap_successfactors', 'FOBusinessUnit', 'FOBusinessUnit', 'Unidad de Negocio',
     'Objeto de fundacion: unidades de negocio', 'full', NULL, 500, 'externalCode',
     'sap_successfactors_extract', TRUE, 'manual',
     '["externalCode","name_defaultValue","status","startDate","endDate","lastModifiedDateTime"]'::jsonb,
     FALSE, NULL),
    ('sap_successfactors', 'FOJobCode', 'FOJobCode', 'Codigo de Puesto',
     'Objeto de fundacion: codigos de puesto', 'full', NULL, 1000, 'externalCode',
     'sap_successfactors_extract', TRUE, 'manual',
     '["externalCode","name_defaultValue","status","startDate","endDate","lastModifiedDateTime"]'::jsonb,
     FALSE, NULL),
    ('sap_successfactors', 'EmpEmploymentTermination', 'EmpEmploymentTermination', 'Baja de Empleo',
     'Terminaciones de empleo (EmpEmploymentTermination)', 'incremental', 'lastModifiedDateTime',
     200, 'userId', 'sap_successfactors_extract', TRUE, 'manual',
     '["userId","endDate","eventReasonExternalCode","lastModifiedDateTime"]'::jsonb,
     FALSE, 'endDate')
ON CONFLICT (cartridge_id, entity) DO UPDATE
    SET odata_entity     = EXCLUDED.odata_entity,
        display_name     = EXCLUDED.display_name,
        description      = EXCLUDED.description,
        mode             = EXCLUDED.mode,
        watermark_field  = EXCLUDED.watermark_field,
        page_size        = EXCLUDED.page_size,
        primary_key      = EXCLUDED.primary_key,
        dag_id           = EXCLUDED.dag_id,
        enabled          = EXCLUDED.enabled,
        select_fields    = EXCLUDED.select_fields,
        effective_dated  = EXCLUDED.effective_dated,
        date_field       = EXCLUDED.date_field;

-- FEMSA AWS scoped schedule. Guarded so generic/local installs without this
-- tenant/workspace keep manual mode instead of pointing to a nonexistent scope.
WITH femsa_scope AS (
    SELECT
        'b95f4d58-c9c8-4fd5-8d07-ddde294c7d78'::uuid AS tenant_id,
        'a2b1ced2-4d92-4bbe-8f9f-9a7cc88bb9f4'::uuid AS workspace_id
    WHERE EXISTS (SELECT 1 FROM tenants WHERE id = 'b95f4d58-c9c8-4fd5-8d07-ddde294c7d78'::uuid)
      AND EXISTS (SELECT 1 FROM workspaces WHERE id = 'a2b1ced2-4d92-4bbe-8f9f-9a7cc88bb9f4'::uuid)
), desired(entity, cron_expression) AS (
    VALUES
        ('FOCompany', '10 3 * * *'),
        ('FODepartment', '20 3 * * *'),
        ('FODivision', '30 3 * * *'),
        ('FOBusinessUnit', '40 3 * * *'),
        ('FOJobCode', '50 3 * * *'),
        ('EmpEmploymentTermination', '0 4 * * *')
)
UPDATE entity_config ec
   SET trigger_type      = 'scheduled',
       cron_expression   = desired.cron_expression,
       connection_id     = 'femsa_sf',
       dag_id            = COALESCE(NULLIF(ec.dag_id, ''), 'sap_successfactors_extract'),
       tenant_id         = femsa_scope.tenant_id,
       workspace_id      = femsa_scope.workspace_id,
       dag_params        = COALESCE(ec.dag_params, '{}'::jsonb)
  FROM desired, femsa_scope
 WHERE ec.cartridge_id = 'sap_successfactors'
   AND ec.entity = desired.entity;

INSERT INTO schema_migrations (filename, applied_at)
VALUES ('99l_sap_successfactors_gold_foundation_schedule.sql', NOW())
ON CONFLICT (filename) DO NOTHING;
