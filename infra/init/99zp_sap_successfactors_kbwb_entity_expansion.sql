-- 99zp_sap_successfactors_kbwb_entity_expansion.sql
--
-- Expands the SuccessFactors catalog with the KBWB PDF talent entities.
-- Historical migrations remain untouched; this one only upserts the delta.

INSERT INTO entity_config
    (cartridge_id, entity, odata_entity, display_name, description, mode,
     watermark_field, page_size, primary_key, select_fields, protection,
     dag_id, enabled, trigger_type, connection_id)
VALUES
    ('sap_successfactors', 'FOPayGrade', 'FOPayGrade', 'Grado de Pago', 'Foundation Object: grados de pago no salariales', 'full', NULL, 1000, 'externalCode',
     '["externalCode","name_defaultValue","status","startDate","endDate","lastModifiedDateTime"]'::jsonb, '{}'::jsonb, 'sap_successfactors_extract', TRUE, 'manual', 'femsa_sf'),
    ('sap_successfactors', 'FormPerfPotSummarySection', 'FormPerfPotSummarySection', 'Resumen Performance/Potential', 'Resumen de desempeno y potencial para 9-box', 'incremental', 'lastModifiedDateTime', 200, 'formDataId',
     '["formDataId","formSubjectId","performanceRating","potentialRating","lastModifiedDateTime"]'::jsonb, '{"formSubjectId":"shadowed"}'::jsonb, 'sap_successfactors_extract', TRUE, 'manual', 'femsa_sf'),
    ('sap_successfactors', 'FormObjective', 'FormObjective', 'Objetivos de Formulario', 'Objetivos dentro de formularios PMGM', 'incremental', 'lastModifiedDateTime', 200, 'objectiveId',
     '["objectiveId","formDataId","userId","name","status","percentComplete","lastModifiedDateTime"]'::jsonb, '{"userId":"shadowed"}'::jsonb, 'sap_successfactors_extract', TRUE, 'manual', 'femsa_sf'),
    ('sap_successfactors', 'FormObjectiveDetails', 'FormObjectiveDetails', 'Detalle de Objetivos', 'Detalle de objetivos PMGM', 'incremental', 'lastModifiedDateTime', 200, 'objectiveDetailId',
     '["objectiveDetailId","objectiveId","formDataId","userId","status","percentComplete","lastModifiedDateTime"]'::jsonb, '{"userId":"shadowed"}'::jsonb, 'sap_successfactors_extract', TRUE, 'manual', 'femsa_sf'),
    ('sap_successfactors', 'SimpleGoal', 'SimpleGoal', 'Objetivo Simple', 'Entidad alternativa de objetivos SimpleGoal', 'incremental', 'lastModifiedDateTime', 200, 'id',
     '["id","userId","name","state","percentComplete","startDate","dueDate","lastModifiedDateTime"]'::jsonb, '{"userId":"shadowed"}'::jsonb, 'sap_successfactors_extract', TRUE, 'manual', 'femsa_sf'),
    ('sap_successfactors', 'GoalAchievements', 'GoalAchievements', 'Logros de Objetivos', 'Avances y logros de objetivos', 'incremental', 'lastModifiedDateTime', 200, 'achievementId',
     '["achievementId","goalId","userId","status","achievementPercent","lastModifiedDateTime"]'::jsonb, '{"userId":"shadowed"}'::jsonb, 'sap_successfactors_extract', TRUE, 'manual', 'femsa_sf'),
    ('sap_successfactors', 'CalibrationSession', 'CalibrationSession', 'Sesion de Calibracion', 'Sesiones de calibracion de talento/desempeno', 'incremental', 'lastModifiedDateTime', 200, 'sessionId',
     '["sessionId","name","status","startDate","endDate","lastModifiedDateTime"]'::jsonb, '{}'::jsonb, 'sap_successfactors_extract', TRUE, 'manual', 'femsa_sf'),
    ('sap_successfactors', 'CalibrationSessionSubject', 'CalibrationSessionSubject', 'Sujetos de Calibracion', 'Empleados incluidos en sesiones de calibracion', 'incremental', 'lastModifiedDateTime', 500, 'subjectId',
     '["subjectId","sessionId","userId","performanceRating","potentialRating","lastModifiedDateTime"]'::jsonb, '{"userId":"shadowed"}'::jsonb, 'sap_successfactors_extract', TRUE, 'manual', 'femsa_sf'),
    ('sap_successfactors', 'CalibrationSubjectRank', 'CalibrationSubjectRank', 'Ranking de Calibracion', 'Ranking/calibracion por sujeto', 'incremental', 'lastModifiedDateTime', 500, 'rankId',
     '["rankId","sessionId","subjectId","userId","rank","lastModifiedDateTime"]'::jsonb, '{"userId":"shadowed"}'::jsonb, 'sap_successfactors_extract', TRUE, 'manual', 'femsa_sf'),
    ('sap_successfactors', 'WorkerCompetencyAssessment', 'WorkerCompetencyAssessment', 'Evaluacion Competencia Trabajador', 'Evaluaciones de competencias por empleado', 'incremental', 'lastModifiedDateTime', 500, 'externalCode',
     '["externalCode","userId","competency","competencyName","rating","proficiency","lastModifiedDateTime"]'::jsonb, '{"userId":"shadowed"}'::jsonb, 'sap_successfactors_extract', TRUE, 'manual', 'femsa_sf'),
    ('sap_successfactors', 'FormCompetency', 'FormCompetency', 'Competencia de Formulario', 'Ratings de competencias en formularios PMGM', 'incremental', 'lastModifiedDateTime', 500, 'formCompetencyId',
     '["formCompetencyId","formDataId","userId","competency","competencyName","rating","lastModifiedDateTime"]'::jsonb, '{"userId":"shadowed"}'::jsonb, 'sap_successfactors_extract', TRUE, 'manual', 'femsa_sf'),
    ('sap_successfactors', 'SysOverallCompetency', 'SysOverallCompetency', 'Competencia Overall', 'Score overall de competencias en PMGM', 'incremental', 'lastModifiedDateTime', 500, 'externalCode',
     '["externalCode","userId","competency","rating","lastModifiedDateTime"]'::jsonb, '{"userId":"shadowed"}'::jsonb, 'sap_successfactors_extract', TRUE, 'manual', 'femsa_sf'),
    ('sap_successfactors', 'SkillEntity', 'SkillEntity', 'Catalogo de Skills', 'Catalogo Talent Intelligence Hub de skills', 'incremental', 'lastModifiedDateTime', 500, 'externalCode',
     '["externalCode","name","description","status","lastModifiedDateTime"]'::jsonb, '{}'::jsonb, 'sap_successfactors_extract', TRUE, 'manual', 'femsa_sf'),
    ('sap_successfactors', 'DevGoal', 'DevGoal', 'Meta de Desarrollo', 'Metas de desarrollo y aspiracion', 'incremental', 'lastModifiedDateTime', 500, 'id',
     '["id","userId","name","status","startDate","dueDate","lastModifiedDateTime"]'::jsonb, '{"userId":"shadowed"}'::jsonb, 'sap_successfactors_extract', TRUE, 'manual', 'femsa_sf'),
    ('sap_successfactors', 'DevGoalCompetency', 'DevGoalCompetency', 'Competencia de Meta', 'Competencias asociadas a metas de desarrollo', 'incremental', 'lastModifiedDateTime', 500, 'externalCode',
     '["externalCode","devGoalId","userId","competency","competencyName","lastModifiedDateTime"]'::jsonb, '{"userId":"shadowed"}'::jsonb, 'sap_successfactors_extract', TRUE, 'manual', 'femsa_sf'),
    ('sap_successfactors', 'TalentPool', 'TalentPool', 'Talent Pool', 'Catalogo de talent pools', 'incremental', 'lastModifiedDateTime', 500, 'poolId',
     '["poolId","name","status","lastModifiedDateTime"]'::jsonb, '{}'::jsonb, 'sap_successfactors_extract', TRUE, 'manual', 'femsa_sf'),
    ('sap_successfactors', 'TalentPoolNav', 'TalentPoolNav', 'Miembros Talent Pool', 'Membresias de empleados en talent pools', 'incremental', 'lastModifiedDateTime', 500, 'externalCode',
     '["externalCode","poolId","userId","readiness","status","lastModifiedDateTime"]'::jsonb, '{"userId":"shadowed"}'::jsonb, 'sap_successfactors_extract', TRUE, 'manual', 'femsa_sf'),
    ('sap_successfactors', 'UserCourses', 'UserCourses', 'Cursos de Usuario', 'Learning v4: cursos asignados por usuario', 'incremental', 'lastModifiedDateTime', 500, 'assignmentId',
     '["assignmentId","userId","courseId","status","dueDate","completionDate","lastModifiedDateTime"]'::jsonb, '{"userId":"shadowed"}'::jsonb, 'sap_successfactors_extract', TRUE, 'manual', 'femsa_sf'),
    ('sap_successfactors', 'UserPrograms', 'UserPrograms', 'Programas de Usuario', 'Learning v4: programas asignados por usuario', 'incremental', 'lastModifiedDateTime', 500, 'assignmentId',
     '["assignmentId","userId","programId","status","dueDate","completionDate","lastModifiedDateTime"]'::jsonb, '{"userId":"shadowed"}'::jsonb, 'sap_successfactors_extract', TRUE, 'manual', 'femsa_sf'),
    ('sap_successfactors', 'LearningEvents', 'LearningEvents', 'Eventos Learning', 'Learning v4: eventos completados', 'incremental', 'lastModifiedDateTime', 500, 'eventId',
     '["eventId","userId","itemId","completionDate","status","lastModifiedDateTime"]'::jsonb, '{"userId":"shadowed"}'::jsonb, 'sap_successfactors_extract', TRUE, 'manual', 'femsa_sf'),
    ('sap_successfactors', 'Curricula', 'Curricula', 'Curricula', 'Learning v4: curriculas/certificaciones', 'incremental', 'lastModifiedDateTime', 500, 'curriculumId',
     '["curriculumId","title","status","expirationDate","lastModifiedDateTime"]'::jsonb, '{}'::jsonb, 'sap_successfactors_extract', TRUE, 'manual', 'femsa_sf'),
    ('sap_successfactors', 'CatalogsFeed', 'CatalogsFeed', 'Catalogo Learning', 'Learning v4: catalog feed', 'incremental', 'lastModifiedDateTime', 500, 'itemId',
     '["itemId","title","status","duration","creditHours","lastModifiedDateTime"]'::jsonb, '{}'::jsonb, 'sap_successfactors_extract', TRUE, 'manual', 'femsa_sf')
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
        trigger_type    = EXCLUDED.trigger_type,
        connection_id   = COALESCE(NULLIF(entity_config.connection_id, ''), EXCLUDED.connection_id);

UPDATE entity_config
SET description = CASE entity
    WHEN 'CareerWorksheet' THEN 'Custom/enrichment career worksheet. Not a standard SuccessFactors C/P/A blocker unless tenant metadata exposes it.'
    WHEN 'CareerInterest' THEN 'Custom/enrichment career interest. Not a standard SuccessFactors C/P/A blocker unless tenant metadata exposes it.'
    ELSE description
END
WHERE cartridge_id = 'sap_successfactors'
  AND entity IN ('CareerWorksheet', 'CareerInterest');

INSERT INTO schema_migrations (filename, applied_at)
VALUES ('99zp_sap_successfactors_kbwb_entity_expansion.sql', NOW())
ON CONFLICT (filename) DO NOTHING;
