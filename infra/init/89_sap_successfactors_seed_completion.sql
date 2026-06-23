-- 89_sap_successfactors_seed_completion.sql
--
-- Complete sap_successfactors' entity_config to the full 30-entity catalog declared
-- in entities.yaml. Migration 79 seeded only the 15 entities active at the time; the
-- other 15 (Per*, EmpPayComp*, EmpEmploymentTermination, FOCompany, FOBusinessUnit,
-- FOJobCode, EmployeeTime, TimeAccount, WorkSchedule) were declared in the YAML but
-- never seeded, so the DB catalog (15) diverged from the YAML (30).
--
-- UPSERTs all 30. The 15 from migration 79 are repeated verbatim (DO UPDATE no-op);
-- the 15 missing are inserted with their YAML metadata and the SF standard primary
-- keys (personIdExternal / userId / externalCode). entity_config has no workspace_id
-- (cartridge-scoped). Idempotent: re-runnable. Scope: sap_successfactors ONLY.

INSERT INTO entity_config
    (cartridge_id, entity, odata_entity, display_name, description, mode,
     watermark_field, page_size, primary_key, dag_id, enabled, trigger_type)
VALUES
    ('sap_successfactors', 'User', 'User', 'Usuarios', 'Datos maestros del usuario (User)', 'incremental', 'lastModifiedDateTime', 200, 'userId', 'sap_successfactors_extract', TRUE, 'manual'),
    ('sap_successfactors', 'EmpEmployment', 'EmpEmployment', 'Empleo', 'Datos de empleo (EmpEmployment)', 'incremental', 'lastModifiedDateTime', 200, 'userId', 'sap_successfactors_extract', TRUE, 'manual'),
    ('sap_successfactors', 'EmpJob', 'EmpJob', 'Puesto (Job)', 'Datos de puesto (EmpJob)', 'incremental', 'lastModifiedDateTime', 200, 'userId', 'sap_successfactors_extract', TRUE, 'manual'),
    ('sap_successfactors', 'EmpCompensation', 'EmpCompensation', 'Compensación', 'Datos de compensación (EmpCompensation)', 'incremental', 'lastModifiedDateTime', 200, 'userId', 'sap_successfactors_extract', TRUE, 'manual'),
    ('sap_successfactors', 'Position', 'Position', 'Posición', 'Datos de posición (Position)', 'incremental', 'lastModifiedDateTime', 500, 'code', 'sap_successfactors_extract', TRUE, 'manual'),
    ('sap_successfactors', 'FODepartment', 'FODepartment', 'Departamento', 'Objeto de fundación: departamentos', 'full', NULL, 1000, 'externalCode', 'sap_successfactors_extract', TRUE, 'manual'),
    ('sap_successfactors', 'FODivision', 'FODivision', 'División', 'Objeto de fundación: divisiones', 'full', NULL, 500, 'externalCode', 'sap_successfactors_extract', TRUE, 'manual'),
    ('sap_successfactors', 'FOLocation', 'FOLocation', 'Ubicación', 'Objeto de fundación: ubicaciones', 'full', NULL, 1000, 'externalCode', 'sap_successfactors_extract', TRUE, 'manual'),
    ('sap_successfactors', 'FOCostCenter', 'FOCostCenter', 'Centro de Costos', 'Objeto de fundación: centros de costo', 'full', NULL, 1000, 'externalCode', 'sap_successfactors_extract', TRUE, 'manual'),
    ('sap_successfactors', 'EmpJob_History', 'EmpJobRelationships', 'Relaciones Laborales', 'Relaciones de puesto (EmpJobRelationships)', 'incremental', 'lastModifiedDateTime', 200, 'userId', 'sap_successfactors_extract', TRUE, 'manual'),
    ('sap_successfactors', 'JobRequisition', 'JobRequisition', 'Requisición de Puesto', 'Requisiciones de empleo (Recruiting)', 'incremental', 'lastModifiedDateTime', 200, 'jobReqId', 'sap_successfactors_extract', TRUE, 'manual'),
    ('sap_successfactors', 'Candidate', 'Candidate', 'Candidatos', 'Candidatos en pipeline (Recruiting)', 'incremental', 'lastModifiedDateTime', 200, 'candidateId', 'sap_successfactors_extract', TRUE, 'manual'),
    ('sap_successfactors', 'LearningItem', 'Item', 'Items de Aprendizaje', 'Items de aprendizaje (LMS, entityset Item)', 'incremental', 'lastModifiedDateTime', 200, 'learningItemId', 'sap_successfactors_extract', TRUE, 'manual'),
    ('sap_successfactors', 'PerformanceReview', 'FormHeader', 'Evaluación de Desempeño', 'Encabezados de formularios de evaluación (PMGM, entityset FormHeader)', 'incremental', 'lastModifiedDateTime', 200, 'formDataId', 'sap_successfactors_extract', TRUE, 'manual'),
    ('sap_successfactors', 'GoalPlan', 'Goal', 'Plan de Objetivos', 'Objetivos de desempeño (entityset Goal)', 'incremental', 'lastModifiedDateTime', 200, 'planId', 'sap_successfactors_extract', TRUE, 'manual'),
    ('sap_successfactors', 'PerPerson', 'PerPerson', 'Persona', 'Informacion personal (PerPerson)', 'incremental', 'lastModifiedDateTime', 200, 'personIdExternal', 'sap_successfactors_extract', TRUE, 'manual'),
    ('sap_successfactors', 'PerPersonal', 'PerPersonal', 'Datos Personales', 'Datos personales efectivo-fechados (PerPersonal)', 'incremental', 'lastModifiedDateTime', 200, 'personIdExternal', 'sap_successfactors_extract', TRUE, 'manual'),
    ('sap_successfactors', 'PerEmail', 'PerEmail', 'Correos', 'Correos de la persona (PerEmail)', 'incremental', 'lastModifiedDateTime', 500, 'personIdExternal', 'sap_successfactors_extract', TRUE, 'manual'),
    ('sap_successfactors', 'PerPhone', 'PerPhone', 'Telefonos', 'Telefonos de la persona (PerPhone)', 'incremental', 'lastModifiedDateTime', 500, 'personIdExternal', 'sap_successfactors_extract', TRUE, 'manual'),
    ('sap_successfactors', 'PerAddressDEFLT', 'PerAddressDEFLT', 'Direcciones', 'Direcciones de la persona (PerAddressDEFLT)', 'incremental', 'lastModifiedDateTime', 500, 'personIdExternal', 'sap_successfactors_extract', TRUE, 'manual'),
    ('sap_successfactors', 'PerNationalId', 'PerNationalId', 'ID Nacional', 'Identificaciones nacionales (PerNationalId)', 'full', NULL, 200, 'personIdExternal', 'sap_successfactors_extract', TRUE, 'manual'),
    ('sap_successfactors', 'EmpPayCompRecurring', 'EmpPayCompRecurring', 'Pago Recurrente', 'Componentes de pago recurrentes (EmpPayCompRecurring)', 'incremental', 'lastModifiedDateTime', 200, 'userId', 'sap_successfactors_extract', TRUE, 'manual'),
    ('sap_successfactors', 'EmpPayCompNonRecurring', 'EmpPayCompNonRecurring', 'Pago No Recurrente', 'Componentes de pago no recurrentes (EmpPayCompNonRecurring)', 'incremental', 'lastModifiedDateTime', 200, 'userId', 'sap_successfactors_extract', TRUE, 'manual'),
    ('sap_successfactors', 'EmpEmploymentTermination', 'EmpEmploymentTermination', 'Baja de Empleo', 'Terminaciones de empleo (EmpEmploymentTermination)', 'incremental', 'lastModifiedDateTime', 200, 'userId', 'sap_successfactors_extract', TRUE, 'manual'),
    ('sap_successfactors', 'FOCompany', 'FOCompany', 'Compania', 'Objeto de fundacion: companias', 'full', NULL, 500, 'externalCode', 'sap_successfactors_extract', TRUE, 'manual'),
    ('sap_successfactors', 'FOBusinessUnit', 'FOBusinessUnit', 'Unidad de Negocio', 'Objeto de fundacion: unidades de negocio', 'full', NULL, 500, 'externalCode', 'sap_successfactors_extract', TRUE, 'manual'),
    ('sap_successfactors', 'FOJobCode', 'FOJobCode', 'Codigo de Puesto', 'Objeto de fundacion: codigos de puesto', 'full', NULL, 1000, 'externalCode', 'sap_successfactors_extract', TRUE, 'manual'),
    ('sap_successfactors', 'EmployeeTime', 'EmployeeTime', 'Tiempo del Empleado', 'Registros de tiempo del empleado (EmployeeTime)', 'incremental', 'lastModifiedDateTime', 500, 'userId', 'sap_successfactors_extract', TRUE, 'manual'),
    ('sap_successfactors', 'TimeAccount', 'TimeAccount', 'Cuenta de Tiempo', 'Cuentas de tiempo (TimeAccount)', 'incremental', 'lastModifiedDateTime', 500, 'userId', 'sap_successfactors_extract', TRUE, 'manual'),
    ('sap_successfactors', 'WorkSchedule', 'WorkSchedule', 'Horario de Trabajo', 'Horarios de trabajo (WorkSchedule)', 'incremental', 'lastModifiedDateTime', 500, 'userId', 'sap_successfactors_extract', TRUE, 'manual')
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
VALUES ('89_sap_successfactors_seed_completion.sql', NOW())
ON CONFLICT (filename) DO NOTHING;
