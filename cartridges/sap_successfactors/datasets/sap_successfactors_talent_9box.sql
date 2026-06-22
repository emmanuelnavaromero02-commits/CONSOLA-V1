-- sap_successfactors_talent_9box  (gold)  cartridge: sap_successfactors
-- sources: ["gold/sap_successfactors/sap_successfactors_talent_readiness"]
-- description: 9-box Talento con contrato estable. Se bloquea hasta contar con performance, competencias y aspiracion.

WITH readiness AS (
    SELECT *
    FROM read_parquet('s3://{bucket}/gold/sap_successfactors/sap_successfactors_talent_readiness/**/*.parquet',
                      hive_partitioning = true,
                      union_by_name = true)
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
    CAST(NULL AS DOUBLE) AS potential_score,
    'insufficient_data' AS performance_band,
    'insufficient_data' AS potential_band,
    'Sin datos C/P/A' AS box_label,
    'blocked' AS box_status,
    blockers,
    CURRENT_TIMESTAMP AS generated_at
FROM readiness
ORDER BY user_id
