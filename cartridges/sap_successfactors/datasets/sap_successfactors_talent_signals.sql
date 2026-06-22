-- sap_successfactors_talent_signals  (gold)  cartridge: sap_successfactors
-- sources: ["gold/sap_successfactors/sap_successfactors_talent_readiness", "gold/sap_successfactors/sap_successfactors_talent_role_profile", "gold/sap_successfactors/sap_successfactors_talent_mobility_history"]
-- description: Senales WisdomBit Talento como recomendaciones. No ejecuta acciones automaticas ni write-back.

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
),
mobility AS (
    SELECT *
    FROM read_parquet('s3://{bucket}/gold/sap_successfactors/sap_successfactors_talent_mobility_history/**/*.parquet',
                      hive_partitioning = true,
                      union_by_name = true)
),
metrics AS (
    SELECT
        (SELECT COUNT(*) FROM readiness WHERE readiness_status = 'insufficient_data') AS insufficient_readiness_count,
        (SELECT COUNT(*) FROM roles WHERE required_skills_status = 'blocked') AS blocked_role_count,
        (SELECT COUNT(*) FROM mobility WHERE movement_events > 0) AS employees_with_mobility_count
)
SELECT
    'talent_cpa_missing_inputs' AS signal_id,
    'priorizacion' AS signal_type,
    'medium' AS severity,
    insufficient_readiness_count AS affected_count,
    'Fit Score bloqueado por falta de C/P/A' AS title,
    'Habilitar desempeno, competencias y aspiracion para calcular readiness real.' AS recommendation,
    'recommendation_only' AS status,
    CURRENT_TIMESTAMP AS generated_at
FROM metrics
WHERE insufficient_readiness_count > 0
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
