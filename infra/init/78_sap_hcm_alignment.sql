-- 78_sap_hcm_alignment.sql
--
-- Phase-2 alignment of sap_hcm's entity_config with its OData contract.
--
-- Problem (same class as S/4HANA pre-migration-77): sap_hcm's entity_config
-- carries the 10 business entities (EmployeeMaster, PersonalData, ...) with no
-- odata_entity, while cartridges/sap_hcm/app/config/entities.yaml keyed those
-- same entities by technical infotype names (PA0001Set, ...). Because the names
-- never matched, the catalog merge could not fill odata_entity, so extraction
-- fell back to fetch_entity("EmployeeMaster") -> 404, and protection_service
-- (which looks up entities.yaml by the business name) silently applied nothing.
--
-- Fix (data-only here; entities.yaml is re-keyed to the business names in the
-- same change): add the odata_filter column and seed the 10 business entities
-- with the technical odata_entity each maps to, inheriting mode / watermark /
-- page_size / primary_key from entities.yaml. OrgUnit / Position / JobCode all
-- resolve to HRORG_OBJECT_SRV/HRP1000Set and are disambiguated by odata_filter
-- (object type), which extraction_service appends to the OData $filter.
--
-- OData paths are the ones already verified in entities.yaml (HRORG_OBJECT_SRV
-- for OM, HRESS_TEAM_SRV/PA2001Set for absences), not the alternatives floated
-- in the task brief.
--
-- Scope: sap_hcm only. The DELETE is filtered by cartridge_id so Replicon,
-- S/4HANA and SuccessFactors rows are untouched. Idempotent (re-runnable).

ALTER TABLE entity_config ADD COLUMN IF NOT EXISTS odata_filter TEXT;

-- Drop any sap_hcm rows that are not part of the canonical business-entity set.
DELETE FROM entity_config
 WHERE cartridge_id = 'sap_hcm'
   AND entity NOT IN (
     'EmployeeMaster', 'PersonalData', 'EmployeeActions', 'ContractData',
     'WorkSchedule', 'LeaveAbsence', 'CostCenter', 'OrgUnit', 'Position', 'JobCode'
   );

INSERT INTO entity_config
    (cartridge_id, entity, odata_entity, odata_filter, display_name, description,
     mode, watermark_field, page_size, primary_key, dag_id, enabled, trigger_type)
VALUES
    ('sap_hcm', 'EmployeeMaster', 'HRPA_EE_PA_SRV/PA0001Set', NULL, 'Maestro de Empleados', 'Asignación organizacional del empleado (infotipo 0001)', 'incremental', 'AedtmAed', 1000, 'Pernr', 'sap_hcm_extract', TRUE, 'manual'),
    ('sap_hcm', 'PersonalData', 'HRPA_EE_PA_SRV/PA0002Set', NULL, 'Datos Personales', 'Datos personales del empleado (infotipo 0002)', 'incremental', 'AedtmAed', 1000, 'Pernr', 'sap_hcm_extract', TRUE, 'manual'),
    ('sap_hcm', 'EmployeeActions', 'HRPA_EE_PA_SRV/PA0000Set', NULL, 'Acciones de Empleados', 'Acciones de personal (infotipo 0000)', 'incremental', 'AedtmAed', 500, 'Pernr', 'sap_hcm_extract', TRUE, 'manual'),
    ('sap_hcm', 'ContractData', 'HRPA_EE_PA_SRV/PA0016Set', NULL, 'Datos de Contrato', 'Elementos del contrato (infotipo 0016)', 'incremental', 'AedtmAed', 500, 'Pernr', 'sap_hcm_extract', TRUE, 'manual'),
    ('sap_hcm', 'WorkSchedule', 'HRPA_EE_PA_SRV/PA0007Set', NULL, 'Horarios', 'Horario de trabajo planificado (infotipo 0007)', 'full', 'AedtmAed', 1000, 'Pernr', 'sap_hcm_extract', TRUE, 'manual'),
    ('sap_hcm', 'LeaveAbsence', 'HRESS_TEAM_SRV/PA2001Set', NULL, 'Ausencias', 'Ausencias y permisos (infotipo 2001)', 'incremental', 'AedtmAed', 1000, 'Pernr', 'sap_hcm_extract', TRUE, 'manual'),
    ('sap_hcm', 'CostCenter', 'HRPA_EE_PA_SRV/PA0001Set', NULL, 'Centro de Costos', 'Asignación de centro de costos (subconjunto del infotipo 0001)', 'full', 'AedtmAed', 1000, 'Kostl', 'sap_hcm_extract', TRUE, 'manual'),
    ('sap_hcm', 'OrgUnit', 'HRORG_OBJECT_SRV/HRP1000Set', 'Otype eq ''O''', 'Unidad Organizacional', 'Unidades organizacionales (objetos OM, tipo O)', 'full', 'AedtmAed', 1000, 'ObjId', 'sap_hcm_extract', TRUE, 'manual'),
    ('sap_hcm', 'Position', 'HRORG_OBJECT_SRV/HRP1000Set', 'Otype eq ''S''', 'Posición', 'Posiciones (objetos OM, tipo S)', 'full', 'AedtmAed', 1000, 'ObjId', 'sap_hcm_extract', TRUE, 'manual'),
    ('sap_hcm', 'JobCode', 'HRORG_OBJECT_SRV/HRP1000Set', 'Otype eq ''C''', 'Código de Trabajo', 'Trabajos (objetos OM, tipo C)', 'full', 'AedtmAed', 1000, 'ObjId', 'sap_hcm_extract', TRUE, 'manual')
ON CONFLICT (cartridge_id, entity) DO UPDATE
    SET odata_entity    = EXCLUDED.odata_entity,
        odata_filter    = EXCLUDED.odata_filter,
        display_name    = EXCLUDED.display_name,
        description     = EXCLUDED.description,
        mode            = EXCLUDED.mode,
        watermark_field = EXCLUDED.watermark_field,
        page_size       = EXCLUDED.page_size,
        primary_key     = EXCLUDED.primary_key,
        dag_id          = EXCLUDED.dag_id,
        enabled         = EXCLUDED.enabled,
        trigger_type    = EXCLUDED.trigger_type;

INSERT INTO schema_migrations (filename, applied_at)
VALUES ('78_sap_hcm_alignment.sql', NOW())
ON CONFLICT (filename) DO NOTHING;
