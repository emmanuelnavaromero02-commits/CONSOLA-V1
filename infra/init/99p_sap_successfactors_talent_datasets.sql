-- 99p_sap_successfactors_talent_datasets.sql
--
-- Existing AWS/local installs already applied 82_sap_successfactors_datasets_seed.sql.
-- This follow-up migration registers the SuccessFactors Talent/WisdomBit Gold
-- datasets without requiring the old seed to be replayed.

CREATE TABLE IF NOT EXISTS schema_migrations (
    id BIGSERIAL PRIMARY KEY,
    filename TEXT NOT NULL UNIQUE,
    applied_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    checksum TEXT
);

INSERT INTO datasets (name, layer, cartridge, sources, sql_def, description, column_mapping, schedule, updated_at, workspace_id)
VALUES
($seed$sap_successfactors_talent_9box$seed$, $seed$gold$seed$, $seed$sap_successfactors$seed$, $seed$["gold/sap_successfactors/sap_successfactors_talent_readiness"]$seed$::jsonb, $seed$
-- sap_successfactors_talent_9box  (gold)  cartridge: sap_successfactors
-- sources: ["gold/sap_successfactors/sap_successfactors_talent_readiness"]
-- description: 9-box Talento WB-TALENTO. Clasifica por desempeno y potencial cuando C/P/A existe; si falta, bloquea la fila.

WITH readiness AS (
    SELECT *
    FROM read_parquet('s3://{bucket}/gold/sap_successfactors/sap_successfactors_talent_readiness/**/*.parquet',
                      hive_partitioning = true,
                      union_by_name = true)
),
scored AS (
    SELECT
        *,
        CASE
            WHEN TRY_CAST(performance_score AS DOUBLE) IS NULL THEN NULL
            WHEN TRY_CAST(performance_score AS DOUBLE) > 5 THEN TRY_CAST(performance_score AS DOUBLE) / 20
            ELSE TRY_CAST(performance_score AS DOUBLE)
        END AS performance_scale,
        CASE
            WHEN TRY_CAST(competency_score AS DOUBLE) IS NULL OR TRY_CAST(aspiration_score AS DOUBLE) IS NULL THEN NULL
            ELSE
                (0.60 * CASE WHEN TRY_CAST(competency_score AS DOUBLE) > 5 THEN TRY_CAST(competency_score AS DOUBLE) / 20 ELSE TRY_CAST(competency_score AS DOUBLE) END)
                + (0.40 * CASE WHEN TRY_CAST(aspiration_score AS DOUBLE) > 5 THEN TRY_CAST(aspiration_score AS DOUBLE) / 20 ELSE TRY_CAST(aspiration_score AS DOUBLE) END)
        END AS potential_scale
    FROM readiness
),
banded AS (
    SELECT
        *,
        CASE
            WHEN performance_scale IS NULL THEN 'insufficient_data'
            WHEN performance_scale >= 4 THEN 'high'
            WHEN performance_scale >= 3 THEN 'medium'
            ELSE 'low'
        END AS performance_band_calc,
        CASE
            WHEN potential_scale IS NULL THEN 'insufficient_data'
            WHEN potential_scale >= 4 THEN 'high'
            WHEN potential_scale >= 3 THEN 'medium'
            ELSE 'low'
        END AS potential_band_calc
    FROM scored
)
SELECT
    user_id,
    full_name,
    company_name,
    department_name,
    location_name,
    job_code,
    role_name,
    performance_score,
    ROUND(potential_scale, 2) AS potential_score,
    performance_band_calc AS performance_band,
    -- Opcion 1: banda "Desempeno disponible" del performance_score REAL (cortes actuales),
    -- nunca proxy; NULL sin desempeno. + cohorte esperando Competencias y Aspiracion.
    CASE
        WHEN performance_scale IS NULL THEN NULL
        WHEN performance_scale >= 4 THEN 'high'
        WHEN performance_scale >= 3 THEN 'medium'
        ELSE 'low'
    END AS performance_band_available,
    CASE WHEN potential_scale IS NULL THEN TRUE ELSE FALSE END AS potential_pending,
    potential_band_calc AS potential_band,
    CASE
        WHEN performance_band_calc = 'insufficient_data' OR potential_band_calc = 'insufficient_data' THEN 'insufficient_data'
        WHEN potential_band_calc = 'high' AND performance_band_calc = 'low' THEN 'enigma'
        WHEN potential_band_calc = 'high' AND performance_band_calc = 'medium' THEN 'crecimiento'
        WHEN potential_band_calc = 'high' AND performance_band_calc = 'high' THEN 'estrella'
        WHEN potential_band_calc = 'medium' AND performance_band_calc = 'low' THEN 'dilema'
        WHEN potential_band_calc = 'medium' AND performance_band_calc = 'medium' THEN 'core'
        WHEN potential_band_calc = 'medium' AND performance_band_calc = 'high' THEN 'alto_impacto'
        WHEN potential_band_calc = 'low' AND performance_band_calc = 'low' THEN 'riesgo'
        WHEN potential_band_calc = 'low' AND performance_band_calc = 'medium' THEN 'efectivo'
        WHEN potential_band_calc = 'low' AND performance_band_calc = 'high' THEN 'experto'
        ELSE 'insufficient_data'
    END AS box_key,
    CASE
        WHEN performance_band_calc = 'insufficient_data' OR potential_band_calc = 'insufficient_data' THEN 'Sin datos C/P/A'
        WHEN potential_band_calc = 'high' AND performance_band_calc = 'low' THEN 'Enigma'
        WHEN potential_band_calc = 'high' AND performance_band_calc = 'medium' THEN 'Crecimiento'
        WHEN potential_band_calc = 'high' AND performance_band_calc = 'high' THEN 'Estrella'
        WHEN potential_band_calc = 'medium' AND performance_band_calc = 'low' THEN 'Dilema'
        WHEN potential_band_calc = 'medium' AND performance_band_calc = 'medium' THEN 'Core'
        WHEN potential_band_calc = 'medium' AND performance_band_calc = 'high' THEN 'Alto Impacto'
        WHEN potential_band_calc = 'low' AND performance_band_calc = 'low' THEN 'Riesgo'
        WHEN potential_band_calc = 'low' AND performance_band_calc = 'medium' THEN 'Efectivo'
        WHEN potential_band_calc = 'low' AND performance_band_calc = 'high' THEN 'Experto'
        ELSE 'Sin datos C/P/A'
    END AS box_label,
    CASE
        WHEN performance_band_calc = 'insufficient_data' OR potential_band_calc = 'insufficient_data' THEN 'blocked'
        ELSE 'ready'
    END AS box_status,
    blockers,
    CURRENT_TIMESTAMP AS generated_at
FROM banded
ORDER BY user_id
$seed$, $seed$9-box Talento WB-TALENTO. Clasifica por desempeno y potencial cuando C/P/A existe; si falta, bloquea la fila.$seed$, $seed${}$seed$::jsonb, $seed$$seed$, NOW(), (SELECT id FROM workspaces ORDER BY created_at ASC LIMIT 1)),
($seed$sap_successfactors_talent_9box_operational$seed$, $seed$gold$seed$, $seed$sap_successfactors$seed$, $seed$["gold/sap_successfactors/sap_successfactors_talent_9box"]$seed$::jsonb, $seed$
-- sap_successfactors_talent_9box_operational  (gold)  cartridge: sap_successfactors
-- sources: ["gold/sap_successfactors/sap_successfactors_talent_9box"]
-- description: Distribucion 9-box agregada para Control Room Talent. No expone PII ni identificadores individuales.

WITH boxes(box_key, potential_band, performance_band, box_label, movement_action, display_order) AS (
    VALUES
        ('enigma', 'high', 'low', 'Enigma', 'Cambio de rol o coaching de fit', 1),
        ('crecimiento', 'high', 'medium', 'Crecimiento', 'Asignacion de estiramiento y rotacion', 2),
        ('estrella', 'high', 'high', 'Estrella', 'Sucesion, promocion y retencion', 3),
        ('dilema', 'medium', 'low', 'Dilema', 'Plan de mejora o reubicacion', 4),
        ('core', 'medium', 'medium', 'Core', 'Retener y desarrollo continuo', 5),
        ('alto_impacto', 'medium', 'high', 'Alto Impacto', 'Promocion a siguiente nivel', 6),
        ('riesgo', 'low', 'low', 'Riesgo', 'PIP o gestion de salida', 7),
        ('efectivo', 'low', 'medium', 'Efectivo', 'Mantener en rol', 8),
        ('experto', 'low', 'high', 'Experto', 'Via tecnica y retencion en rol', 9)
),
rows AS (
    SELECT *
    FROM read_parquet('s3://{bucket}/gold/sap_successfactors/sap_successfactors_talent_9box/**/*.parquet',
                      hive_partitioning = true,
                      union_by_name = true)
),
metrics AS (
    SELECT
        box_key,
        COUNT(*) AS employee_count,
        COUNT(*) FILTER (WHERE box_status = 'ready') AS ready_count,
        COUNT(*) FILTER (WHERE box_status != 'ready') AS blocked_count
    FROM rows
    GROUP BY box_key
)
SELECT
    boxes.box_key,
    boxes.box_label,
    boxes.potential_band,
    boxes.performance_band,
    boxes.movement_action,
    COALESCE(metrics.employee_count, 0) AS employee_count,
    COALESCE(metrics.ready_count, 0) AS ready_count,
    COALESCE(metrics.blocked_count, 0) AS blocked_count,
    CASE WHEN COALESCE(metrics.ready_count, 0) > 0 THEN 'ready' ELSE 'blocked' END AS box_status,
    boxes.display_order,
    CURRENT_TIMESTAMP AS generated_at
FROM boxes
LEFT JOIN metrics ON metrics.box_key = boxes.box_key
ORDER BY boxes.display_order
$seed$, $seed$Distribucion 9-box agregada para Control Room Talent. No expone PII ni identificadores individuales.$seed$, $seed${}$seed$::jsonb, $seed$$seed$, NOW(), (SELECT id FROM workspaces ORDER BY created_at ASC LIMIT 1)),
($seed$sap_successfactors_talent_action_candidates$seed$, $seed$gold$seed$, $seed$sap_successfactors$seed$, $seed$["gold/sap_successfactors/sap_successfactors_talent_readiness", "gold/sap_successfactors/sap_successfactors_talent_9box_operational", "gold/sap_successfactors/sap_successfactors_talent_retention_risk", "gold/sap_successfactors/sap_successfactors_talent_promotion_alignment", "gold/sap_successfactors/sap_successfactors_talent_calibration_sensitivity", "gold/sap_successfactors/sap_successfactors_talent_role_fit_assignments"]$seed$::jsonb, $seed$
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
    'promotion_alignment_heuristic' AS method,
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
    'role_fit_heuristic' AS method,
    CURRENT_TIMESTAMP AS generated_at
FROM metrics
WHERE role_fit_review_count > 0
$seed$, $seed$Candidatos de accion WB-TALENTO para Control Room. Solo recomendaciones; sin write-back.$seed$, $seed${}$seed$::jsonb, $seed$$seed$, NOW(), (SELECT id FROM workspaces ORDER BY created_at ASC LIMIT 1)),
($seed$sap_successfactors_talent_calibration_sensitivity$seed$, $seed$gold$seed$, $seed$sap_successfactors$seed$, $seed$["gold/sap_successfactors/sap_successfactors_talent_9box"]$seed$::jsonb, $seed$
-- sap_successfactors_talent_calibration_sensitivity  (gold)  cartridge: sap_successfactors
-- sources: ["gold/sap_successfactors/sap_successfactors_talent_9box"]
-- description: Sensibilidad de cortes 9-box. Marca personas cerca de los umbrales 3.0 y 4.0 sin exponer PII.

WITH nine_box AS (
    SELECT *
    FROM read_parquet('s3://{bucket}/gold/sap_successfactors/sap_successfactors_talent_9box/**/*.parquet',
                      hive_partitioning = true,
                      union_by_name = true)
),
scored AS (
    SELECT
        box_key,
        box_status,
        CASE
            WHEN TRY_CAST(performance_score AS DOUBLE) IS NULL THEN NULL
            WHEN TRY_CAST(performance_score AS DOUBLE) > 5 THEN TRY_CAST(performance_score AS DOUBLE) / 20
            ELSE TRY_CAST(performance_score AS DOUBLE)
        END AS performance_scale,
        TRY_CAST(potential_score AS DOUBLE) AS potential_scale
    FROM nine_box
),
metrics AS (
    SELECT
        COUNT(*) AS employee_count,
        COUNT(*) FILTER (WHERE box_status = 'ready') AS classified_count,
        COUNT(*) FILTER (
            WHERE box_status = 'ready'
              AND (
                    ABS(performance_scale - 3.0) <= 0.30
                 OR ABS(performance_scale - 4.0) <= 0.30
                 OR ABS(potential_scale - 3.0) <= 0.30
                 OR ABS(potential_scale - 4.0) <= 0.30
              )
        ) AS near_cut_count
    FROM scored
)
SELECT
    employee_count,
    classified_count,
    near_cut_count,
    CASE
        WHEN classified_count = 0 THEN NULL
        ELSE ROUND(near_cut_count * 100.0 / classified_count, 2)
    END AS near_cut_pct,
    CASE WHEN classified_count = 0 THEN 'blocked' ELSE 'recommendation_only' END AS status,
    CASE WHEN classified_count = 0 THEN '["9-box blocked until C/P/A exists"]' ELSE '[]' END AS blockers,
    CURRENT_TIMESTAMP AS generated_at
FROM metrics
$seed$, $seed$Sensibilidad de cortes 9-box. Marca personas cerca de los umbrales 3.0 y 4.0 sin exponer PII.$seed$, $seed${}$seed$::jsonb, $seed$$seed$, NOW(), (SELECT id FROM workspaces ORDER BY created_at ASC LIMIT 1)),
($seed$sap_successfactors_talent_cpa_scores$seed$, $seed$gold$seed$, $seed$sap_successfactors$seed$, $seed$["gold/sap_successfactors/sap_successfactors_talent_employee_profile", "gold/sap_successfactors/sap_successfactors_talent_role_profile"]$seed$::jsonb, $seed$
-- sap_successfactors_talent_cpa_scores  (gold)  cartridge: sap_successfactors
-- sources: ["gold/sap_successfactors/sap_successfactors_talent_employee_profile", "gold/sap_successfactors/sap_successfactors_talent_role_profile"]
-- description: C/P/A normalizado y Fit Score WB-TALENTO. Calcula solo si competencia, desempeno y aspiracion existen; si no, conserva insufficient_data.

WITH emp AS (
    SELECT *
    FROM read_parquet('s3://{bucket}/gold/sap_successfactors/sap_successfactors_talent_employee_profile/**/*.parquet',
                      hive_partitioning = true,
                      union_by_name = true)
),
roles AS (
    SELECT job_code, role_name, role_profile_status, required_skills_status
    FROM read_parquet('s3://{bucket}/gold/sap_successfactors/sap_successfactors_talent_role_profile/**/*.parquet',
                      hive_partitioning = true,
                      union_by_name = true)
),
normalized AS (
    SELECT
        emp.user_id,
        emp.full_name,
        emp.company_name,
        emp.department_name,
        emp.location_name,
        emp.job_code,
        COALESCE(roles.role_name, emp.job_code) AS role_name,
        TRY_CAST(emp.competency_score AS DOUBLE) AS competency_score,
        TRY_CAST(emp.performance_score AS DOUBLE) AS performance_score,
        TRY_CAST(emp.aspiration_score AS DOUBLE) AS aspiration_score,
        CASE
            WHEN TRY_CAST(emp.competency_score AS DOUBLE) IS NULL THEN NULL
            WHEN TRY_CAST(emp.competency_score AS DOUBLE) <= 5 THEN TRY_CAST(emp.competency_score AS DOUBLE) * 20
            ELSE TRY_CAST(emp.competency_score AS DOUBLE)
        END AS competency_100,
        CASE
            WHEN TRY_CAST(emp.performance_score AS DOUBLE) IS NULL THEN NULL
            WHEN TRY_CAST(emp.performance_score AS DOUBLE) <= 5 THEN TRY_CAST(emp.performance_score AS DOUBLE) * 20
            ELSE TRY_CAST(emp.performance_score AS DOUBLE)
        END AS performance_100,
        CASE
            WHEN TRY_CAST(emp.aspiration_score AS DOUBLE) IS NULL THEN NULL
            WHEN TRY_CAST(emp.aspiration_score AS DOUBLE) <= 5 THEN TRY_CAST(emp.aspiration_score AS DOUBLE) * 20
            ELSE TRY_CAST(emp.aspiration_score AS DOUBLE)
        END AS aspiration_100,
        COALESCE(roles.role_profile_status, 'partial') AS role_profile_status,
        COALESCE(roles.required_skills_status, 'blocked') AS required_skills_status,
        emp.blockers AS source_blockers
    FROM emp
    LEFT JOIN roles ON roles.job_code = emp.job_code
)
SELECT
    user_id,
    full_name,
    company_name,
    department_name,
    location_name,
    job_code,
    role_name,
    competency_score,
    performance_score,
    aspiration_score,
    competency_100,
    performance_100,
    aspiration_100,
    CASE
        WHEN competency_100 IS NULL OR performance_100 IS NULL OR aspiration_100 IS NULL THEN NULL
        ELSE ROUND((0.45 * competency_100) + (0.30 * performance_100) + (0.25 * aspiration_100), 2)
    END AS fit_score,
    CASE
        WHEN competency_100 IS NULL OR performance_100 IS NULL OR aspiration_100 IS NULL THEN 'insufficient_data'
        WHEN required_skills_status = 'blocked' THEN 'partial'
        ELSE 'ready'
    END AS cpa_status,
    role_profile_status,
    required_skills_status,
    CASE
        WHEN competency_100 IS NULL OR performance_100 IS NULL OR aspiration_100 IS NULL
            -- GATE 3 (Fase B): blockers condicionales por componente (cada KB solo si su score falta).
            THEN to_json(list_filter([
                    CASE WHEN competency_100 IS NULL THEN 'KB-COMPETENCIAS blocked' END,
                    CASE WHEN performance_100 IS NULL THEN 'KB-DESEMPENO blocked' END,
                    CASE WHEN aspiration_100 IS NULL THEN 'KB-ASPIRACION blocked' END
                ], x -> x IS NOT NULL))::VARCHAR
        WHEN required_skills_status = 'blocked'
            THEN '["Position requirements pending","Skills/competencies metadata pending"]'
        ELSE '[]'
    END AS blockers,
    CURRENT_TIMESTAMP AS generated_at
FROM normalized
ORDER BY user_id
$seed$, $seed$C/P/A normalizado y Fit Score WB-TALENTO. Calcula solo si competencia, desempeno y aspiracion existen; si no, conserva insufficient_data.$seed$, $seed${}$seed$::jsonb, $seed$$seed$, NOW(), (SELECT id FROM workspaces ORDER BY created_at ASC LIMIT 1)),
($seed$sap_successfactors_talent_employee_profile$seed$, $seed$gold$seed$, $seed$sap_successfactors$seed$, $seed$["gold/sap_successfactors/sap_successfactors_employee_360", "gold/sap_successfactors/sap_successfactors_manager_hierarchy"]$seed$::jsonb, $seed$
-- sap_successfactors_talent_employee_profile  (gold)  cartridge: sap_successfactors
-- sources: ["gold/sap_successfactors/sap_successfactors_employee_360", "gold/sap_successfactors/sap_successfactors_manager_hierarchy"]
-- description: Perfil Talento v1: empleado activo + estructura + jerarquia. C/P/A queda explicitamente en insufficient_data hasta activar entidades SAP de talento.

WITH emp AS (
    SELECT *
    FROM read_parquet('s3://{bucket}/gold/sap_successfactors/sap_successfactors_employee_360/**/*.parquet',
                      hive_partitioning = true,
                      union_by_name = true)
    WHERE is_active = TRUE
),
hier AS (
    SELECT user_id, direct_reports, depth
    FROM read_parquet('s3://{bucket}/gold/sap_successfactors/sap_successfactors_manager_hierarchy/**/*.parquet',
                      hive_partitioning = true,
                      union_by_name = true)
)
SELECT
    emp.user_id,
    emp.full_name,
    emp.company_id,
    emp.company_name,
    emp.division_id,
    emp.division_name,
    emp.department_id,
    emp.department_name,
    emp.location_id,
    emp.location_name,
    emp.job_code,
    emp.manager_id,
    COALESCE(hier.direct_reports, 0) AS direct_reports,
    COALESCE(hier.depth, 0) AS hierarchy_depth,
    emp.start_date,
    emp.end_date,
    CASE
        WHEN emp.start_date IS NULL THEN NULL
        ELSE DATE_DIFF('month', TRY_CAST(emp.start_date AS DATE), CURRENT_DATE)
    END AS tenure_months,
    CAST(NULL AS DOUBLE) AS competency_score,
    CAST(NULL AS DOUBLE) AS performance_score,
    CAST(NULL AS DOUBLE) AS aspiration_score,
    'insufficient_data' AS cpa_status,
    'foundation_ready' AS profile_status,
    -- GATE 3 (Fase B): blockers condicionales por componente (stub foundation-safe:
    -- los 3 scores son NULL, asi que emite los 3; mismo patron que cpa_scores).
    to_json(list_filter([
        CASE WHEN competency_score IS NULL THEN 'KB-COMPETENCIAS blocked' END,
        CASE WHEN performance_score IS NULL THEN 'KB-DESEMPENO blocked' END,
        CASE WHEN aspiration_score IS NULL THEN 'KB-ASPIRACION blocked' END
    ], x -> x IS NOT NULL))::VARCHAR AS blockers,
    CURRENT_TIMESTAMP AS generated_at
FROM emp
LEFT JOIN hier ON hier.user_id = emp.user_id
ORDER BY emp.user_id
$seed$, $seed$Perfil Talento v1: empleado activo + estructura + jerarquia. C/P/A queda explicitamente en insufficient_data hasta activar entidades SAP de talento.$seed$, $seed${}$seed$::jsonb, $seed$$seed$, NOW(), (SELECT id FROM workspaces ORDER BY created_at ASC LIMIT 1)),
($seed$sap_successfactors_talent_mobility_history$seed$, $seed$gold$seed$, $seed$sap_successfactors$seed$, $seed$["silver/sap_successfactors/sap_successfactors_empjob_latest", "gold/sap_successfactors/sap_successfactors_employee_360"]$seed$::jsonb, $seed$
-- sap_successfactors_talent_mobility_history  (gold)  cartridge: sap_successfactors
-- sources: ["silver/sap_successfactors/sap_successfactors_empjob_latest", "gold/sap_successfactors/sap_successfactors_employee_360"]
-- description: Movilidad basica desde historico efectivo de EmpJob. Sirve como senal observada, no como aspiracion declarada.

WITH job_history AS (
    SELECT *
    FROM read_parquet('s3://{bucket}/silver/sap_successfactors/sap_successfactors_empjob_latest/**/*.parquet',
                      hive_partitioning = true,
                      union_by_name = true)
    WHERE user_id IS NOT NULL
),
latest_event AS (
    SELECT
        user_id,
        event_reason AS latest_event_reason
    FROM (
        SELECT
            user_id,
            event_reason,
            ROW_NUMBER() OVER (
                PARTITION BY user_id
                ORDER BY TRY_CAST(start_date AS DATE) DESC NULLS LAST
            ) AS rn
        FROM job_history
    )
    WHERE rn = 1
),
rollup AS (
    SELECT
        user_id,
        MIN(TRY_CAST(start_date AS DATE)) AS first_assignment_date,
        MAX(TRY_CAST(start_date AS DATE)) AS latest_assignment_date,
        GREATEST(COUNT(*) - 1, 0) AS movement_events,
        COUNT(DISTINCT department) AS distinct_departments,
        COUNT(DISTINCT location) AS distinct_locations,
        COUNT(DISTINCT job_code) AS distinct_job_codes,
        COUNT(DISTINCT manager_id) AS distinct_managers
    FROM job_history
    GROUP BY user_id
),
emp AS (
    SELECT user_id, full_name, company_name, department_name, location_name, job_code, is_active
    FROM read_parquet('s3://{bucket}/gold/sap_successfactors/sap_successfactors_employee_360/**/*.parquet',
                      hive_partitioning = true,
                      union_by_name = true)
)
SELECT
    rollup.user_id,
    emp.full_name,
    emp.company_name,
    emp.department_name,
    emp.location_name,
    emp.job_code,
    emp.is_active,
    rollup.first_assignment_date,
    rollup.latest_assignment_date,
    rollup.movement_events,
    rollup.distinct_departments,
    rollup.distinct_locations,
    rollup.distinct_job_codes,
    rollup.distinct_managers,
    latest_event.latest_event_reason,
    'observed_from_empjob' AS mobility_status,
    CURRENT_TIMESTAMP AS generated_at
FROM rollup
LEFT JOIN emp ON emp.user_id = rollup.user_id
LEFT JOIN latest_event ON latest_event.user_id = rollup.user_id
ORDER BY rollup.movement_events DESC, rollup.user_id
$seed$, $seed$Movilidad basica desde historico efectivo de EmpJob. Sirve como senal observada, no como aspiracion declarada.$seed$, $seed${}$seed$::jsonb, $seed$$seed$, NOW(), (SELECT id FROM workspaces ORDER BY created_at ASC LIMIT 1)),
($seed$sap_successfactors_talent_promotion_alignment$seed$, $seed$gold$seed$, $seed$sap_successfactors$seed$, $seed$["gold/sap_successfactors/sap_successfactors_talent_9box", "gold/sap_successfactors/sap_successfactors_talent_mobility_history"]$seed$::jsonb, $seed$
-- sap_successfactors_talent_promotion_alignment  (gold)  cartridge: sap_successfactors
-- sources: ["gold/sap_successfactors/sap_successfactors_talent_9box", "gold/sap_successfactors/sap_successfactors_talent_mobility_history"]
-- description: Alineacion de promociones contra 9-box usando eventReason observado. Si no hay C/P/A o promociones, queda parcial.

WITH nine_box AS (
    SELECT user_id, box_key, box_label, box_status
    FROM read_parquet('s3://{bucket}/gold/sap_successfactors/sap_successfactors_talent_9box/**/*.parquet',
                      hive_partitioning = true,
                      union_by_name = true)
),
mobility AS (
    SELECT user_id, latest_event_reason
    FROM read_parquet('s3://{bucket}/gold/sap_successfactors/sap_successfactors_talent_mobility_history/**/*.parquet',
                      hive_partitioning = true,
                      union_by_name = true)
),
promotions AS (
    SELECT
        nine_box.box_key,
        nine_box.box_label,
        nine_box.box_status,
        COUNT(*) AS promotion_count,
        COUNT(*) FILTER (WHERE nine_box.box_key IN ('estrella', 'crecimiento', 'alto_impacto')) AS aligned_count,
        COUNT(*) FILTER (WHERE nine_box.box_key NOT IN ('estrella', 'crecimiento', 'alto_impacto')) AS misaligned_count
    FROM mobility
    JOIN nine_box ON nine_box.user_id = mobility.user_id
    WHERE LOWER(COALESCE(mobility.latest_event_reason, '')) LIKE '%promot%'
       OR LOWER(COALESCE(mobility.latest_event_reason, '')) LIKE '%promotion%'
       OR LOWER(COALESCE(mobility.latest_event_reason, '')) LIKE '%promo%'
    GROUP BY nine_box.box_key, nine_box.box_label, nine_box.box_status
)
SELECT
    COALESCE(box_key, 'no_promotions_observed') AS box_key,
    COALESCE(box_label, 'Sin promociones observadas') AS box_label,
    COALESCE(promotion_count, 0) AS promotion_count,
    COALESCE(aligned_count, 0) AS aligned_count,
    COALESCE(misaligned_count, 0) AS misaligned_count,
    CASE
        WHEN COALESCE(promotion_count, 0) = 0 THEN 'partial'
        WHEN box_status = 'ready' THEN 'ready'
        ELSE 'blocked'
    END AS status,
    CURRENT_TIMESTAMP AS generated_at
FROM promotions
UNION ALL
SELECT
    'summary' AS box_key,
    'Promociones vs calibracion' AS box_label,
    COALESCE(SUM(promotion_count), 0) AS promotion_count,
    COALESCE(SUM(aligned_count), 0) AS aligned_count,
    COALESCE(SUM(misaligned_count), 0) AS misaligned_count,
    CASE WHEN COALESCE(SUM(promotion_count), 0) = 0 THEN 'partial' ELSE 'ready' END AS status,
    CURRENT_TIMESTAMP AS generated_at
FROM promotions
$seed$, $seed$Alineacion de promociones contra 9-box usando eventReason observado. Si no hay C/P/A o promociones, queda parcial.$seed$, $seed${}$seed$::jsonb, $seed$$seed$, NOW(), (SELECT id FROM workspaces ORDER BY created_at ASC LIMIT 1)),
($seed$sap_successfactors_talent_readiness$seed$, $seed$gold$seed$, $seed$sap_successfactors$seed$, $seed$["gold/sap_successfactors/sap_successfactors_talent_cpa_scores"]$seed$::jsonb, $seed$
-- sap_successfactors_talent_readiness  (gold)  cartridge: sap_successfactors
-- sources: ["gold/sap_successfactors/sap_successfactors_talent_cpa_scores"]
-- description: Readiness Talento WB-TALENTO. Calcula Ready/Near/Not cuando C/P/A existe; si falta, devuelve insufficient_data.

WITH cpa AS (
    SELECT *
    FROM read_parquet('s3://{bucket}/gold/sap_successfactors/sap_successfactors_talent_cpa_scores/**/*.parquet',
                      hive_partitioning = true,
                      union_by_name = true)
)
SELECT
    cpa.user_id,
    cpa.full_name,
    cpa.company_name,
    cpa.department_name,
    cpa.location_name,
    cpa.job_code,
    cpa.role_name,
    cpa.competency_score,
    cpa.performance_score,
    cpa.aspiration_score,
    cpa.fit_score,
    CASE
        WHEN cpa.fit_score IS NULL THEN 'insufficient_data'
        WHEN cpa.fit_score >= 80 THEN 'ready'
        WHEN cpa.fit_score >= 60 THEN 'near'
        ELSE 'not_ready'
    END AS readiness_status,
    CASE
        WHEN cpa.fit_score IS NULL THEN 'Insufficient data'
        WHEN cpa.fit_score >= 80 THEN 'Ready'
        WHEN cpa.fit_score >= 60 THEN 'Near'
        ELSE 'Not ready'
    END AS readiness_label,
    cpa.role_profile_status,
    cpa.required_skills_status,
    CASE
        WHEN cpa.fit_score IS NULL THEN 3
        WHEN cpa.required_skills_status = 'blocked' THEN 1
        ELSE 0
    END AS blocker_count,
    cpa.blockers,
    CURRENT_TIMESTAMP AS generated_at
FROM cpa
ORDER BY cpa.user_id
$seed$, $seed$Readiness Talento WB-TALENTO. Calcula Ready/Near/Not cuando C/P/A existe; si falta, devuelve insufficient_data.$seed$, $seed${}$seed$::jsonb, $seed$$seed$, NOW(), (SELECT id FROM workspaces ORDER BY created_at ASC LIMIT 1)),
($seed$sap_successfactors_talent_retention_risk$seed$, $seed$gold$seed$, $seed$sap_successfactors$seed$, $seed$["gold/sap_successfactors/sap_successfactors_talent_readiness", "gold/sap_successfactors/sap_successfactors_talent_mobility_history"]$seed$::jsonb, $seed$
-- sap_successfactors_talent_retention_risk  (gold)  cartridge: sap_successfactors
-- sources: ["gold/sap_successfactors/sap_successfactors_talent_readiness", "gold/sap_successfactors/sap_successfactors_talent_mobility_history"]
-- description: Riesgo de salida recomendativo sin compensacion. Usa Fit Score y estancamiento solo cuando hay datos suficientes.

WITH readiness AS (
    SELECT *
    FROM read_parquet('s3://{bucket}/gold/sap_successfactors/sap_successfactors_talent_readiness/**/*.parquet',
                      hive_partitioning = true,
                      union_by_name = true)
),
mobility AS (
    SELECT user_id, movement_events, latest_assignment_date
    FROM read_parquet('s3://{bucket}/gold/sap_successfactors/sap_successfactors_talent_mobility_history/**/*.parquet',
                      hive_partitioning = true,
                      union_by_name = true)
)
SELECT
    readiness.user_id,
    readiness.company_name,
    readiness.department_name,
    readiness.location_name,
    readiness.job_code,
    readiness.role_name,
    readiness.readiness_status,
    readiness.fit_score,
    COALESCE(mobility.movement_events, 0) AS movement_events,
    CASE
        WHEN mobility.latest_assignment_date IS NULL THEN NULL
        ELSE DATE_DIFF('month', TRY_CAST(mobility.latest_assignment_date AS DATE), CURRENT_DATE)
    END AS months_since_movement,
    CASE
        WHEN readiness.fit_score IS NULL THEN NULL
        ELSE ROUND(
            LEAST(100, GREATEST(0,
                (100 - readiness.fit_score) * 0.55
                + CASE
                    WHEN mobility.latest_assignment_date IS NULL THEN 20
                    WHEN DATE_DIFF('month', TRY_CAST(mobility.latest_assignment_date AS DATE), CURRENT_DATE) >= 24 THEN 30
                    WHEN DATE_DIFF('month', TRY_CAST(mobility.latest_assignment_date AS DATE), CURRENT_DATE) >= 12 THEN 15
                    ELSE 5
                  END
            )),
            2
        )
    END AS retention_risk_score,
    CASE
        WHEN readiness.fit_score IS NULL THEN 'insufficient_data'
        WHEN retention_risk_score >= 70 THEN 'high'
        WHEN retention_risk_score >= 45 THEN 'medium'
        ELSE 'low'
    END AS risk_band,
    CASE WHEN readiness.fit_score IS NULL THEN 'blocked' ELSE 'recommendation_only' END AS status,
    CASE WHEN readiness.fit_score IS NULL THEN '["C/P/A missing for retention risk"]' ELSE '[]' END AS blockers,
    CURRENT_TIMESTAMP AS generated_at
FROM readiness
LEFT JOIN mobility ON mobility.user_id = readiness.user_id
ORDER BY retention_risk_score DESC NULLS LAST, readiness.user_id
$seed$, $seed$Riesgo de salida recomendativo sin compensacion. Usa Fit Score y estancamiento solo cuando hay datos suficientes.$seed$, $seed${}$seed$::jsonb, $seed$$seed$, NOW(), (SELECT id FROM workspaces ORDER BY created_at ASC LIMIT 1)),
($seed$sap_successfactors_talent_role_fit_assignments$seed$, $seed$gold$seed$, $seed$sap_successfactors$seed$, $seed$["gold/sap_successfactors/sap_successfactors_talent_readiness", "gold/sap_successfactors/sap_successfactors_talent_role_profile"]$seed$::jsonb, $seed$
-- sap_successfactors_talent_role_fit_assignments  (gold)  cartridge: sap_successfactors
-- sources: ["gold/sap_successfactors/sap_successfactors_talent_readiness", "gold/sap_successfactors/sap_successfactors_talent_role_profile"]
-- description: Candidatos de movilidad por Fit Score. Recomendativo y bloqueado si faltan requisitos de rol.

WITH readiness AS (
    SELECT *
    FROM read_parquet('s3://{bucket}/gold/sap_successfactors/sap_successfactors_talent_readiness/**/*.parquet',
                      hive_partitioning = true,
                      union_by_name = true)
),
roles AS (
    SELECT *
    FROM read_parquet('s3://{bucket}/gold/sap_successfactors/sap_successfactors_talent_role_profile/**/*.parquet',
                      hive_partitioning = true,
                      union_by_name = true)
)
SELECT
    readiness.user_id,
    readiness.company_name,
    readiness.department_name,
    readiness.location_name,
    readiness.job_code AS current_job_code,
    readiness.role_name AS current_role_name,
    readiness.fit_score,
    readiness.readiness_status,
    roles.required_skills_status,
    CASE
        WHEN readiness.fit_score IS NULL THEN NULL
        WHEN readiness.fit_score < 60 THEN 'review_role_fit'
        WHEN readiness.fit_score >= 80 THEN 'succession_pool'
        ELSE 'development_plan'
    END AS assignment_recommendation,
    CASE
        WHEN readiness.fit_score IS NULL THEN 'blocked'
        WHEN roles.required_skills_status = 'blocked' THEN 'partial'
        ELSE 'recommendation_only'
    END AS status,
    CASE
        WHEN readiness.fit_score IS NULL THEN '["C/P/A missing for role fit"]'
        WHEN roles.required_skills_status = 'blocked' THEN '["Role required skills pending"]'
        ELSE '[]'
    END AS blockers,
    CURRENT_TIMESTAMP AS generated_at
FROM readiness
LEFT JOIN roles ON roles.job_code = readiness.job_code
ORDER BY fit_score DESC NULLS LAST, readiness.user_id
$seed$, $seed$Candidatos de movilidad por Fit Score. Recomendativo y bloqueado si faltan requisitos de rol.$seed$, $seed${}$seed$::jsonb, $seed$$seed$, NOW(), (SELECT id FROM workspaces ORDER BY created_at ASC LIMIT 1)),
($seed$sap_successfactors_talent_role_profile$seed$, $seed$gold$seed$, $seed$sap_successfactors$seed$, $seed$["gold/sap_successfactors/sap_successfactors_employee_360", "silver/sap_successfactors/sap_successfactors_fojobcode_latest"]$seed$::jsonb, $seed$
-- sap_successfactors_talent_role_profile  (gold)  cartridge: sap_successfactors
-- sources: ["gold/sap_successfactors/sap_successfactors_employee_360", "silver/sap_successfactors/sap_successfactors_fojobcode_latest"]
-- description: Perfil de rol derivado desde job_code/FOJobCode. Requisitos de skills quedan bloqueados hasta validar metadata del tenant.

WITH emp AS (
    SELECT *
    FROM read_parquet('s3://{bucket}/gold/sap_successfactors/sap_successfactors_employee_360/**/*.parquet',
                      hive_partitioning = true,
                      union_by_name = true)
),
roles AS (
    SELECT job_code, job_name
    FROM read_parquet('s3://{bucket}/silver/sap_successfactors/sap_successfactors_fojobcode_latest/**/*.parquet',
                      hive_partitioning = true,
                      union_by_name = true)
),
rollup AS (
    SELECT
        emp.job_code,
        COUNT(*) AS employee_count,
        COUNT(*) FILTER (WHERE emp.is_active = TRUE) AS active_employee_count,
        COUNT(DISTINCT emp.department_id) AS departments_count,
        COUNT(DISTINCT emp.location_id) AS locations_count,
        COUNT(DISTINCT emp.company_id) AS companies_count
    FROM emp
    WHERE emp.job_code IS NOT NULL
    GROUP BY emp.job_code
)
SELECT
    rollup.job_code,
    COALESCE(roles.job_name, rollup.job_code) AS role_name,
    rollup.employee_count,
    rollup.active_employee_count,
    rollup.departments_count,
    rollup.locations_count,
    rollup.companies_count,
    'blocked' AS required_skills_status,
    'partial' AS role_profile_status,
    '["Position requirements pending","Skills/competencies metadata pending"]' AS blockers,
    CURRENT_TIMESTAMP AS generated_at
FROM rollup
LEFT JOIN roles ON roles.job_code = rollup.job_code
ORDER BY rollup.active_employee_count DESC, rollup.job_code
$seed$, $seed$Perfil de rol derivado desde job_code/FOJobCode. Requisitos de skills quedan bloqueados hasta validar metadata del tenant.$seed$, $seed${}$seed$::jsonb, $seed$$seed$, NOW(), (SELECT id FROM workspaces ORDER BY created_at ASC LIMIT 1)),
($seed$sap_successfactors_talent_signals$seed$, $seed$gold$seed$, $seed$sap_successfactors$seed$, $seed$["gold/sap_successfactors/sap_successfactors_talent_action_candidates", "gold/sap_successfactors/sap_successfactors_talent_role_profile", "gold/sap_successfactors/sap_successfactors_talent_mobility_history"]$seed$::jsonb, $seed$
-- sap_successfactors_talent_signals  (gold)  cartridge: sap_successfactors
-- sources: ["gold/sap_successfactors/sap_successfactors_talent_action_candidates", "gold/sap_successfactors/sap_successfactors_talent_role_profile", "gold/sap_successfactors/sap_successfactors_talent_mobility_history"]
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
metrics AS (
    SELECT
        (SELECT COUNT(*) FROM roles WHERE required_skills_status = 'blocked') AS blocked_role_count,
        (SELECT COUNT(*) FROM mobility WHERE movement_events > 0) AS employees_with_mobility_count
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
$seed$, $seed$Senales WisdomBit Talento como recomendaciones. No ejecuta acciones automaticas ni write-back.$seed$, $seed${}$seed$::jsonb, $seed$$seed$, NOW(), (SELECT id FROM workspaces ORDER BY created_at ASC LIMIT 1))
ON CONFLICT DO NOTHING;

INSERT INTO schema_migrations (filename, applied_at)
VALUES ('99p_sap_successfactors_talent_datasets.sql', NOW())
ON CONFLICT (filename) DO NOTHING;
