-- 99zb_sap_successfactors_talent_entities.sql
--
-- Completes the SuccessFactors talent PoC/enrichment entity catalog from the
-- WB-TALENTO PDF: C/P/A, role requirements, learning, recruiting applications
-- and movement event reasons. These entities are tenant-specific and extract_all
-- only runs them when live $metadata/preflight marks them available.

INSERT INTO entity_config
    (cartridge_id, entity, odata_entity, display_name, description, mode,
     watermark_field, page_size, primary_key, select_fields, protection,
     dag_id, enabled, trigger_type)
VALUES
    ('sap_successfactors', 'FOEventReason', 'FOEventReason', 'Razon de Evento', 'Foundation Object: razones de evento para movimientos y bajas', 'full', NULL, 1000, 'externalCode',
     '["externalCode","name_defaultValue","event","eventReasonCategory","status","startDate","endDate","lastModifiedDateTime"]'::jsonb, '{}'::jsonb, 'sap_successfactors_extract', TRUE, 'manual'),
    ('sap_successfactors', 'JobApplication', 'JobApplication', 'Aplicacion Recruiting', 'Aplicaciones que unen candidato, requisicion y etapa', 'incremental', 'lastModifiedDateTime', 200, 'applicationId',
     '["applicationId","jobReqId","candidateId","applicationStatus","source","lastModifiedDateTime"]'::jsonb, '{"candidateId":"shadowed"}'::jsonb, 'sap_successfactors_extract', TRUE, 'manual'),
    ('sap_successfactors', 'PerformanceReview', 'FormHeader', 'Evaluacion de Desempeno', 'Encabezados de formularios de evaluacion con rating', 'incremental', 'lastModifiedDateTime', 200, 'formDataId',
     '["formDataId","formSubjectId","formTemplateId","status","overallRating","potentialRating","formStartDate","formEndDate","lastModifiedDateTime"]'::jsonb, '{"formSubjectId":"shadowed"}'::jsonb, 'sap_successfactors_extract', TRUE, 'manual'),
    ('sap_successfactors', 'GoalPlan', 'Goal', 'Plan de Objetivos', 'Objetivos de desempeno para avance y cobertura', 'incremental', 'lastModifiedDateTime', 200, 'id',
     '["id","userId","name","state","percentComplete","startDate","dueDate","lastModifiedDateTime"]'::jsonb, '{"userId":"shadowed"}'::jsonb, 'sap_successfactors_extract', TRUE, 'manual'),
    ('sap_successfactors', 'CompetencyEntity', 'CompetencyEntity', 'Competencia', 'Catalogo tenant de competencias', 'incremental', 'lastModifiedDateTime', 500, 'externalCode',
     '["externalCode","name","description","status","lastModifiedDateTime"]'::jsonb, '{}'::jsonb, 'sap_successfactors_extract', TRUE, 'manual'),
    ('sap_successfactors', 'UserSkill', 'UserSkill', 'Skill de Usuario', 'Skills/proficiencies por empleado', 'incremental', 'lastModifiedDateTime', 500, 'externalCode',
     '["externalCode","userId","skill","skillName","proficiency","rating","lastModifiedDateTime"]'::jsonb, '{"userId":"shadowed"}'::jsonb, 'sap_successfactors_extract', TRUE, 'manual'),
    ('sap_successfactors', 'SkillProfile', 'SkillProfile', 'Perfil de Skill', 'Entidad alternativa de skills/proficiencies por empleado', 'incremental', 'lastModifiedDateTime', 500, 'externalCode',
     '["externalCode","userId","skill","skillName","proficiency","rating","lastModifiedDateTime"]'::jsonb, '{"userId":"shadowed"}'::jsonb, 'sap_successfactors_extract', TRUE, 'manual'),
    ('sap_successfactors', 'CareerWorksheet', 'CareerWorksheet', 'Career Worksheet', 'Roles objetivo y readiness declarada', 'incremental', 'lastModifiedDateTime', 500, 'externalCode',
     '["externalCode","userId","role","jobRole","readiness","lastModifiedDateTime"]'::jsonb, '{"userId":"shadowed"}'::jsonb, 'sap_successfactors_extract', TRUE, 'manual'),
    ('sap_successfactors', 'CareerInterest', 'CareerInterest', 'Interes de Carrera', 'Intereses de carrera y preferencias de movilidad', 'incremental', 'lastModifiedDateTime', 500, 'externalCode',
     '["externalCode","userId","jobRole","interest","mobilityPreference","lastModifiedDateTime"]'::jsonb, '{"userId":"shadowed"}'::jsonb, 'sap_successfactors_extract', TRUE, 'manual'),
    ('sap_successfactors', 'SuccessionNomination', 'SuccessionNomination', 'Nominacion Sucesion', 'Nominaciones de sucesion y readiness', 'incremental', 'lastModifiedDateTime', 500, 'externalCode',
     '["externalCode","userId","position","readiness","nominationStatus","lastModifiedDateTime"]'::jsonb, '{"userId":"shadowed"}'::jsonb, 'sap_successfactors_extract', TRUE, 'manual'),
    ('sap_successfactors', 'LearningItem', 'Item', 'Items de Aprendizaje', 'Catalogo LMS de items de aprendizaje', 'incremental', 'lastModifiedDateTime', 200, 'learningItemId',
     '["learningItemId","itemId","title","status","creditHours","duration","expirationDate","lastModifiedDateTime"]'::jsonb, '{}'::jsonb, 'sap_successfactors_extract', TRUE, 'manual'),
    ('sap_successfactors', 'LearningAssignment', 'LearningAssignment', 'Asignacion Learning', 'Asignaciones LMS por empleado y item', 'incremental', 'lastModifiedDateTime', 500, 'assignmentId',
     '["assignmentId","userId","itemId","status","dueDate","completionDate","lastModifiedDateTime"]'::jsonb, '{"userId":"shadowed"}'::jsonb, 'sap_successfactors_extract', TRUE, 'manual'),
    ('sap_successfactors', 'LearningHistory', 'LearningHistory', 'Historial Learning', 'Historial LMS completado y certificaciones', 'incremental', 'lastModifiedDateTime', 500, 'historyId',
     '["historyId","userId","itemId","completionDate","creditHours","status","lastModifiedDateTime"]'::jsonb, '{"userId":"shadowed"}'::jsonb, 'sap_successfactors_extract', TRUE, 'manual')
ON CONFLICT (cartridge_id, entity) DO UPDATE
    SET odata_entity    = EXCLUDED.odata_entity,
        display_name    = EXCLUDED.display_name,
        description     = EXCLUDED.description,
        mode            = EXCLUDED.mode,
        watermark_field = EXCLUDED.watermark_field,
        page_size       = EXCLUDED.page_size,
        primary_key     = EXCLUDED.primary_key,
        select_fields   = EXCLUDED.select_fields,
        protection      = EXCLUDED.protection,
        dag_id          = EXCLUDED.dag_id,
        enabled         = EXCLUDED.enabled,
        trigger_type    = EXCLUDED.trigger_type;

INSERT INTO schema_migrations (filename, applied_at)
VALUES ('99zb_sap_successfactors_talent_entities.sql', NOW())
ON CONFLICT (filename) DO NOTHING;
