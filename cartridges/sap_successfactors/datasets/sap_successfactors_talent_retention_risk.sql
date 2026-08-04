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
),
scored AS (
    SELECT
        readiness.user_id,
        readiness.company_name,
        readiness.department_name,
        readiness.location_name,
        readiness.job_code,
        readiness.role_name,
        readiness.readiness_status,
        CASE
            WHEN COALESCE(readiness.invalid_score_input, TRUE)
              OR NOT COALESCE(
                  readiness.readiness_status IN ('ready', 'near', 'not_ready'), FALSE
              )
              OR NOT talent_percent_is_valid(readiness.fit_score) THEN NULL
            ELSE readiness.fit_score
        END AS fit_score,
        (
            COALESCE(readiness.invalid_score_input, TRUE)
            OR NOT COALESCE(
                readiness.readiness_status IN ('ready', 'near', 'not_ready'), FALSE
            )
            OR NOT talent_percent_is_valid(readiness.fit_score)
        ) AS invalid_score_input,
        COALESCE(mobility.movement_events, 0) AS movement_events,
        CASE
            WHEN mobility.latest_assignment_date IS NULL THEN NULL
            ELSE DATE_DIFF('month', TRY_CAST(mobility.latest_assignment_date AS DATE), CURRENT_DATE)
        END AS months_since_movement,
        CASE
            WHEN COALESCE(readiness.invalid_score_input, TRUE)
              OR NOT COALESCE(
                  readiness.readiness_status IN ('ready', 'near', 'not_ready'), FALSE
              )
              OR NOT talent_percent_is_valid(readiness.fit_score) THEN NULL
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
        END AS retention_risk_score
    FROM readiness
    LEFT JOIN mobility ON mobility.user_id = readiness.user_id
)
SELECT
    user_id,
    company_name,
    department_name,
    location_name,
    job_code,
    role_name,
    readiness_status,
    fit_score,
    invalid_score_input,
    movement_events,
    months_since_movement,
    retention_risk_score,
    CASE
        WHEN invalid_score_input THEN 'insufficient_data'
        WHEN retention_risk_score >= 70 THEN 'high'
        WHEN retention_risk_score >= 45 THEN 'medium'
        ELSE 'low'
    END AS risk_band,
    CASE WHEN invalid_score_input THEN 'blocked' ELSE 'recommendation_only' END AS status,
    CASE WHEN invalid_score_input THEN '["C/P/A invalid or missing for retention risk"]' ELSE '[]' END AS blockers,
    CURRENT_TIMESTAMP AS generated_at
FROM scored
ORDER BY retention_risk_score DESC NULLS LAST, user_id
