-- 79_sap_successfactors_alignment.sql
--
-- Align sap_successfactors' entity_config with its OData contract and
-- entities.yaml (same class of fix as migrations 77/78 for S/4HANA and HCM).
--
-- Problems:
--  1. Foundation Object rows were seeded under the wrong business names
--     (Department / Division / Location / CostCenter) while entities.yaml and
--     the real OData entitysets use the FO* names (FODepartment, FODivision,
--     FOLocation, FOCostCenter). The names never matched, so the catalog merge
--     could not fill odata_entity and the knowledge bits read the wrong bronze
--     folder.
--  2. The talent entities (JobRequisition, Candidate, GoalPlan, PerformanceReview,
--     LearningItem) and EmpJob_History existed in entity_config but not in
--     entities.yaml, and several have a business name that differs from the OData
--     entityset (GoalPlan->Goal, PerformanceReview->FormHeader, LearningItem->Item,
--     EmpJob_History->EmpJobRelationships).
--
-- Fix (data only; extraction_service already resolves the path via
-- config.get("odata_entity", entity)): add the odata_entity column, drop the
-- four misnamed FO rows so they can be recreated under the FO* names, and UPSERT
-- the 15 canonical entities with their odata_entity. Business names are the
-- bronze-path component raw/sap_successfactors/{entity}/ and what the KBs read.
--
-- Note on LearningItem: odata_entity 'Item' is the SuccessFactors LMS standard
-- entityset default. It is NOT externally verified for this tenant — flagged in
-- the PR for follow-up; the business name and bronze path stay 'LearningItem'.
--
-- Scope: sap_successfactors ONLY (filtered by cartridge_id). Replicon, sap_hcm
-- and sap_s4hana are untouched. Idempotent (re-runnable).

ALTER TABLE entity_config ADD COLUMN IF NOT EXISTS odata_entity TEXT;

-- Rename the misnamed Foundation Object rows by removing the old keys; the
-- canonical FO* rows are recreated by the UPSERT below.
DELETE FROM entity_config
 WHERE cartridge_id = 'sap_successfactors'
   AND entity IN ('Department', 'Division', 'Location', 'CostCenter');

INSERT INTO entity_config
    (cartridge_id, entity, odata_entity, display_name, description, mode,
     watermark_field, page_size, primary_key, dag_id, enabled, trigger_type)
VALUES
    ('sap_successfactors', 'User', 'User', 'Usuarios', 'Datos maestros del usuario (User)', 'incremental', 'lastModifiedDateTime', 200, 'userId', 'sap_successfactors_extract', TRUE, 'manual'),
    ('sap_successfactors', 'EmpEmployment', 'EmpEmployment', 'Empleo', 'Datos de empleo (EmpEmployment)', 'incremental', 'lastModifiedDateTime', 200, 'userId', 'sap_successfactors_extract', TRUE, 'manual'),
    ('sap_successfactors', 'EmpJob', 'EmpJob', 'Puesto (Job)', 'Datos de puesto (EmpJob)', 'incremental', 'lastModifiedDateTime', 200, 'userId', 'sap_successfactors_extract', TRUE, 'manual'),
    ('sap_successfactors', 'EmpCompensation', 'EmpCompensation', 'Compensación', 'Datos de compensación (EmpCompensation)', 'incremental', 'lastModifiedDateTime', 200, 'userId', 'sap_successfactors_extract', TRUE, 'manual'),
    ('sap_successfactors', 'Position', 'Position', 'Posición', 'Datos de posición (Position)', 'incremental', 'lastModifiedDateTime', 500, 'positionCode', 'sap_successfactors_extract', TRUE, 'manual'),
    ('sap_successfactors', 'FODepartment', 'FODepartment', 'Departamento', 'Objeto de fundación: departamentos', 'full', NULL, 1000, 'externalCode', 'sap_successfactors_extract', TRUE, 'manual'),
    ('sap_successfactors', 'FODivision', 'FODivision', 'División', 'Objeto de fundación: divisiones', 'full', NULL, 500, 'externalCode', 'sap_successfactors_extract', TRUE, 'manual'),
    ('sap_successfactors', 'FOLocation', 'FOLocation', 'Ubicación', 'Objeto de fundación: ubicaciones', 'full', NULL, 1000, 'externalCode', 'sap_successfactors_extract', TRUE, 'manual'),
    ('sap_successfactors', 'FOCostCenter', 'FOCostCenter', 'Centro de Costos', 'Objeto de fundación: centros de costo', 'full', NULL, 1000, 'externalCode', 'sap_successfactors_extract', TRUE, 'manual'),
    ('sap_successfactors', 'EmpJob_History', 'EmpJobRelationships', 'Relaciones Laborales', 'Relaciones de puesto (EmpJobRelationships)', 'incremental', 'lastModifiedDateTime', 200, 'userId', 'sap_successfactors_extract', TRUE, 'manual'),
    ('sap_successfactors', 'JobRequisition', 'JobRequisition', 'Requisición de Puesto', 'Requisiciones de empleo (Recruiting)', 'incremental', 'lastModifiedDateTime', 200, 'jobReqId', 'sap_successfactors_extract', TRUE, 'manual'),
    ('sap_successfactors', 'Candidate', 'Candidate', 'Candidatos', 'Candidatos en pipeline (Recruiting)', 'incremental', 'lastModifiedDateTime', 200, 'candidateId', 'sap_successfactors_extract', TRUE, 'manual'),
    ('sap_successfactors', 'LearningItem', 'Item', 'Items de Aprendizaje', 'Items de aprendizaje (LMS, entityset Item)', 'incremental', 'lastModifiedDateTime', 200, 'learningItemId', 'sap_successfactors_extract', TRUE, 'manual'),
    ('sap_successfactors', 'PerformanceReview', 'FormHeader', 'Evaluación de Desempeño', 'Encabezados de formularios de evaluación (PMGM, entityset FormHeader)', 'incremental', 'lastModifiedDateTime', 200, 'formDataId', 'sap_successfactors_extract', TRUE, 'manual'),
    ('sap_successfactors', 'GoalPlan', 'Goal', 'Plan de Objetivos', 'Objetivos de desempeño (entityset Goal)', 'incremental', 'lastModifiedDateTime', 200, 'planId', 'sap_successfactors_extract', TRUE, 'manual')
ON CONFLICT (cartridge_id, entity) DO UPDATE
    SET odata_entity    = EXCLUDED.odata_entity,
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
VALUES ('79_sap_successfactors_alignment.sql', NOW())
ON CONFLICT (filename) DO NOTHING;
