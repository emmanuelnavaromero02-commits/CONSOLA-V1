-- sap_successfactors_talent_signals  (gold)  cartridge: sap_successfactors
-- sources: ["gold/sap_successfactors/sap_successfactors_talent_action_candidates", "gold/sap_successfactors/sap_successfactors_talent_role_profile", "gold/sap_successfactors/sap_successfactors_talent_mobility_history", "gold/sap_successfactors/sap_successfactors_talent_competency_skill_gap", "gold/sap_successfactors/sap_successfactors_talent_learning_certification_status", "gold/sap_successfactors/sap_successfactors_recruitment_application_funnel"]
-- description: Senales WisdomBit Talento como recomendaciones. No ejecuta acciones automaticas ni write-back.

WITH actions AS (
    SELECT *
    FROM read_parquet('s3://{bucket}/gold/sap_successfactors/sap_successfactors_talent_action_candidates/**/*.parquet',
                      hive_partitioning = true,
                      union_by_name = true)
),
roles AS (
    SELECT *
    FROM read_parquet('s3://{bucket}/gold/sap_successfactors/sap_successfactors_talent_role_profile/**/*.parquet',
                      hive_partitioning = true,
                      union_by_name = true)
),
mobility AS (
    SELECT *
    FROM read_parquet('s3://{bucket}/gold/sap_successfactors/sap_successfactors_talent_mobility_history/**/*.parquet',
                      hive_partitioning = true,
                      union_by_name = true)
),
skill_gap AS (
    SELECT *
    FROM read_parquet('s3://{bucket}/gold/sap_successfactors/sap_successfactors_talent_competency_skill_gap/**/*.parquet',
                      hive_partitioning = true,
                      union_by_name = true)
),
learning AS (
    SELECT *
    FROM read_parquet('s3://{bucket}/gold/sap_successfactors/sap_successfactors_talent_learning_certification_status/**/*.parquet',
                      hive_partitioning = true,
                      union_by_name = true)
),
recruiting AS (
    SELECT *
    FROM read_parquet('s3://{bucket}/gold/sap_successfactors/sap_successfactors_recruitment_application_funnel/**/*.parquet',
                      hive_partitioning = true,
                      union_by_name = true)
),
metrics AS (
    SELECT
        (SELECT COUNT(*) FROM roles WHERE required_skills_status = 'blocked') AS blocked_role_count,
        (SELECT COUNT(*) FROM mobility WHERE movement_events > 0) AS employees_with_mobility_count,
        (SELECT COUNT(*) FROM skill_gap WHERE skill_gap_status IN ('insufficient_data', 'partial')) AS skill_gap_count,
        (SELECT SUM(overdue_events) FROM learning) AS overdue_learning_events,
        (SELECT COUNT(*) FROM recruiting WHERE application_funnel_status = 'ready') AS recruiting_stage_count
)
SELECT
    action_id AS signal_id,
    action_type AS signal_type,
    severity,
    affected_count,
    title,
    recommendation,
    status,
    CURRENT_TIMESTAMP AS generated_at
FROM actions
UNION ALL
SELECT
    'talent_role_requirements_missing' AS signal_id,
    'pipeline' AS signal_type,
    'medium' AS severity,
    blocked_role_count AS affected_count,
    'Requisitos de rol pendientes' AS title,
    'Validar Position y entidades de skills para comparar persona contra rol.' AS recommendation,
    'recommendation_only' AS status,
    CURRENT_TIMESTAMP AS generated_at
FROM metrics
WHERE blocked_role_count > 0
UNION ALL
SELECT
    'talent_mobility_observed' AS signal_id,
    'asignacion' AS signal_type,
    'low' AS severity,
    employees_with_mobility_count AS affected_count,
    'Movilidad observada disponible' AS title,
    'Usar historial EmpJob como proxy temporal mientras aspiracion declarada queda pendiente.' AS recommendation,
    'recommendation_only' AS status,
    CURRENT_TIMESTAMP AS generated_at
FROM metrics
WHERE employees_with_mobility_count > 0
UNION ALL
SELECT
    'talent_skill_gap_pending' AS signal_id,
    'priorizacion' AS signal_type,
    'medium' AS severity,
    skill_gap_count AS affected_count,
    'Brechas de skills pendientes' AS title,
    'Completar skills/proficiencies y matriz rol-skill para calcular gaps reales.' AS recommendation,
    'recommendation_only' AS status,
    CURRENT_TIMESTAMP AS generated_at
FROM metrics
WHERE skill_gap_count > 0
UNION ALL
SELECT
    'talent_learning_overdue' AS signal_id,
    'asignacion' AS signal_type,
    'low' AS severity,
    COALESCE(overdue_learning_events, 0) AS affected_count,
    'Aprendizaje vencido observado' AS title,
    'Revisar cursos/certificaciones vencidas antes de proponer movimientos.' AS recommendation,
    'recommendation_only' AS status,
    CURRENT_TIMESTAMP AS generated_at
FROM metrics
WHERE COALESCE(overdue_learning_events, 0) > 0
UNION ALL
SELECT
    'talent_recruiting_stages_ready' AS signal_id,
    'pipeline' AS signal_type,
    'low' AS severity,
    recruiting_stage_count AS affected_count,
    'Etapas de recruiting disponibles' AS title,
    'Usar JobApplication para explicar cobertura interna/externa y etapa de candidatos.' AS recommendation,
    'recommendation_only' AS status,
    CURRENT_TIMESTAMP AS generated_at
FROM metrics
WHERE recruiting_stage_count > 0
