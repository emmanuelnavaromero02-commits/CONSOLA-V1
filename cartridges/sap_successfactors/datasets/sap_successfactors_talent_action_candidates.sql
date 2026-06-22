-- sap_successfactors_talent_action_candidates  (gold)  cartridge: sap_successfactors
-- sources: ["gold/sap_successfactors/sap_successfactors_talent_readiness", "gold/sap_successfactors/sap_successfactors_talent_9box_operational", "gold/sap_successfactors/sap_successfactors_talent_retention_risk", "gold/sap_successfactors/sap_successfactors_talent_promotion_alignment", "gold/sap_successfactors/sap_successfactors_talent_calibration_sensitivity", "gold/sap_successfactors/sap_successfactors_talent_role_fit_assignments"]
-- description: Candidatos de accion WB-TALENTO para Control Room. Solo recomendaciones; sin write-back.

WITH readiness AS (
    SELECT *
    FROM read_parquet('s3://{bucket}/gold/sap_successfactors/sap_successfactors_talent_readiness/**/*.parquet',
                      hive_partitioning = true,
                      union_by_name = true)
),
nine_box AS (
    SELECT *
    FROM read_parquet('s3://{bucket}/gold/sap_successfactors/sap_successfactors_talent_9box_operational/**/*.parquet',
                      hive_partitioning = true,
                      union_by_name = true)
),
risk AS (
    SELECT *
    FROM read_parquet('s3://{bucket}/gold/sap_successfactors/sap_successfactors_talent_retention_risk/**/*.parquet',
                      hive_partitioning = true,
                      union_by_name = true)
),
promotion AS (
    SELECT *
    FROM read_parquet('s3://{bucket}/gold/sap_successfactors/sap_successfactors_talent_promotion_alignment/**/*.parquet',
                      hive_partitioning = true,
                      union_by_name = true)
),
sensitivity AS (
    SELECT *
    FROM read_parquet('s3://{bucket}/gold/sap_successfactors/sap_successfactors_talent_calibration_sensitivity/**/*.parquet',
                      hive_partitioning = true,
                      union_by_name = true)
),
role_fit AS (
    SELECT *
    FROM read_parquet('s3://{bucket}/gold/sap_successfactors/sap_successfactors_talent_role_fit_assignments/**/*.parquet',
                      hive_partitioning = true,
                      union_by_name = true)
),
metrics AS (
    SELECT
        (SELECT COUNT(*) FROM readiness) AS employee_count,
        (SELECT COUNT(*) FROM readiness WHERE readiness_status = 'insufficient_data') AS insufficient_count,
        (SELECT COALESCE(SUM(employee_count), 0) FROM nine_box WHERE box_status = 'ready') AS classified_count,
        (SELECT COUNT(*) FROM risk WHERE risk_band = 'high') AS high_risk_count,
        (SELECT COALESCE(SUM(misaligned_count), 0) FROM promotion WHERE box_key = 'summary') AS misaligned_promotion_count,
        (SELECT COALESCE(MAX(near_cut_count), 0) FROM sensitivity) AS near_cut_count,
        (SELECT COUNT(*) FROM role_fit WHERE assignment_recommendation = 'review_role_fit') AS role_fit_review_count
)
SELECT
    'talent_cpa_missing_inputs' AS action_id,
    'priorizacion' AS action_type,
    'medium' AS severity,
    insufficient_count AS affected_count,
    'Fit Score bloqueado por falta de C/P/A' AS title,
    'Habilitar desempeno, competencias y aspiracion para calcular readiness real.' AS recommendation,
    'recommendation_only' AS status,
    'metadata_preflight' AS method,
    CURRENT_TIMESTAMP AS generated_at
FROM metrics
WHERE insufficient_count > 0
UNION ALL
SELECT
    'talent_9box_operational_ready' AS action_id,
    'asignacion' AS action_type,
    'low' AS severity,
    classified_count AS affected_count,
    '9-box operativo disponible' AS title,
    'Usar matriz 9-box para priorizar sucesion, desarrollo y movilidad.' AS recommendation,
    'recommendation_only' AS status,
    'nine_box' AS method,
    CURRENT_TIMESTAMP AS generated_at
FROM metrics
WHERE classified_count > 0
UNION ALL
SELECT
    'talent_retention_risk' AS action_id,
    'riesgo_salida' AS action_type,
    CASE WHEN high_risk_count > 0 THEN 'high' ELSE 'low' END AS severity,
    high_risk_count AS affected_count,
    'Riesgo de salida sin compensacion sensible' AS title,
    'Revisar estancamiento y Fit Score antes de proponer retencion.' AS recommendation,
    'recommendation_only' AS status,
    'retention_risk' AS method,
    CURRENT_TIMESTAMP AS generated_at
FROM metrics
WHERE high_risk_count > 0
UNION ALL
SELECT
    'talent_promotion_alignment' AS action_id,
    'sesgo' AS action_type,
    CASE WHEN misaligned_promotion_count > 0 THEN 'high' ELSE 'low' END AS severity,
    misaligned_promotion_count AS affected_count,
    'Promociones fuera de calibracion' AS title,
    'Revisar promociones observadas contra cajas de alto potencial.' AS recommendation,
    'recommendation_only' AS status,
    'permutation' AS method,
    CURRENT_TIMESTAMP AS generated_at
FROM metrics
WHERE misaligned_promotion_count > 0
UNION ALL
SELECT
    'talent_calibration_sensitivity' AS action_id,
    'sensibilidad' AS action_type,
    CASE WHEN near_cut_count > 0 THEN 'medium' ELSE 'low' END AS severity,
    near_cut_count AS affected_count,
    'Casos cerca de cortes 9-box' AS title,
    'Revisar casos cercanos a 3.0/4.0 antes de cerrar calibracion.' AS recommendation,
    'recommendation_only' AS status,
    'cut_sensitivity' AS method,
    CURRENT_TIMESTAMP AS generated_at
FROM metrics
WHERE near_cut_count > 0
UNION ALL
SELECT
    'talent_role_fit_assignments' AS action_id,
    'asignacion' AS action_type,
    CASE WHEN role_fit_review_count > 0 THEN 'medium' ELSE 'low' END AS severity,
    role_fit_review_count AS affected_count,
    'Movilidad por fit de rol' AS title,
    'Evaluar cambio de rol antes de PIP cuando el ajuste sea bajo.' AS recommendation,
    'recommendation_only' AS status,
    'assignment' AS method,
    CURRENT_TIMESTAMP AS generated_at
FROM metrics
WHERE role_fit_review_count > 0
