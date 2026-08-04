-- sap_successfactors_talent_action_candidates  (gold)  cartridge: sap_successfactors
-- sources: ["gold/sap_successfactors/sap_successfactors_talent_readiness", "gold/sap_successfactors/sap_successfactors_talent_9box", "gold/sap_successfactors/sap_successfactors_talent_retention_risk", "gold/sap_successfactors/sap_successfactors_talent_role_fit_assignments", "gold/sap_successfactors/sap_successfactors_talent_mobility_history"]
-- description: Candidatos de accion WB-TALENTO para Control Room. Solo recomendaciones; sin write-back.

WITH readiness AS (
    SELECT *
    FROM read_parquet('s3://{bucket}/gold/sap_successfactors/sap_successfactors_talent_readiness/**/*.parquet',
                      hive_partitioning = true,
                      union_by_name = true)
),
nine_box_detail AS (
    SELECT *
    FROM read_parquet('s3://{bucket}/gold/sap_successfactors/sap_successfactors_talent_9box/**/*.parquet',
                      hive_partitioning = true,
                      union_by_name = true)
),
risk AS (
    SELECT *
    FROM read_parquet('s3://{bucket}/gold/sap_successfactors/sap_successfactors_talent_retention_risk/**/*.parquet',
                      hive_partitioning = true,
                      union_by_name = true)
),
mobility AS (
    SELECT *
    FROM read_parquet('s3://{bucket}/gold/sap_successfactors/sap_successfactors_talent_mobility_history/**/*.parquet',
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
        (SELECT COUNT(*) FROM readiness
          WHERE readiness_status = 'insufficient_data'
            AND NOT COALESCE(invalid_score_input, TRUE)) AS insufficient_count,
        (SELECT COUNT(*) FROM nine_box_detail
          WHERE box_status = 'ready'
            AND NOT COALESCE(invalid_score_input, FALSE)
            AND talent_percent_is_valid(performance_score)
            AND talent_percent_is_valid(potential_score)) AS classified_count,
        (SELECT COUNT(*) FROM risk
          WHERE risk_band = 'high'
            AND status = 'recommendation_only'
            AND NOT COALESCE(invalid_score_input, TRUE)
            AND talent_percent_is_valid(fit_score)) AS high_risk_count,
        (SELECT COUNT(*) FROM nine_box_detail AS nb, mobility AS mv
          WHERE mv.user_id = nb.user_id
            AND nb.box_status = 'ready'
            AND NOT COALESCE(nb.invalid_score_input, TRUE)
            AND talent_percent_is_valid(nb.performance_score)
            AND talent_percent_is_valid(nb.potential_score)
            AND nb.box_key NOT IN ('estrella', 'crecimiento', 'alto_impacto')
            AND LOWER(COALESCE(mv.latest_event_reason, '')) LIKE '%promo%') AS misaligned_promotion_count,
        (SELECT COUNT(*) FROM nine_box_detail AS nb
          WHERE nb.box_status = 'ready'
            AND NOT COALESCE(nb.invalid_score_input, TRUE)
            AND talent_percent_is_valid(nb.performance_score)
            AND talent_percent_is_valid(nb.potential_score)
            AND (
                ABS(talent_percent_scale(nb.performance_score) - 3.0) <= 0.30
             OR ABS(talent_percent_scale(nb.performance_score) - 4.0) <= 0.30
             OR ABS(talent_percent_scale(nb.potential_score) - 3.0) <= 0.30
             OR ABS(talent_percent_scale(nb.potential_score) - 4.0) <= 0.30
            )) AS near_cut_count,
        (SELECT COUNT(*) FROM role_fit
          WHERE assignment_recommendation = 'review_role_fit'
            AND status IN ('recommendation_only', 'partial')
            AND NOT COALESCE(invalid_score_input, TRUE)
            AND talent_percent_is_valid(fit_score)) AS role_fit_review_count
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
    'server_validated_v1' AS source_validation_status,
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
    'server_validated_v1' AS source_validation_status,
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
    'server_validated_v1' AS source_validation_status,
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
    'server_validated_v1' AS source_validation_status,
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
    'server_validated_v1' AS source_validation_status,
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
    'server_validated_v1' AS source_validation_status,
    CURRENT_TIMESTAMP AS generated_at
FROM metrics
WHERE role_fit_review_count > 0
