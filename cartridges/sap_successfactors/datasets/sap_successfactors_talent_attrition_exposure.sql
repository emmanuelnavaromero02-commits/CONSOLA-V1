-- sap_successfactors_talent_attrition_exposure  (gold)  cartridge: sap_successfactors
-- sources: ["silver/sap_successfactors/sap_successfactors_empcompensation_latest", "gold/sap_successfactors/sap_successfactors_talent_retention_risk"]
-- description: Exposicion monetaria anualizada agregada por unidad, banda de riesgo y moneda (solo grupos de 5+ personas, sin llaves de empleado). PENDIENTE: los importes de compensacion llegan encrypted desde bronze; sin importes en claro aprobados el dataset no emite filas.

WITH compensation_headers AS (
    SELECT user_id
    FROM read_parquet('s3://{bucket}/silver/sap_successfactors/sap_successfactors_empcompensation_latest/**/*.parquet',
                      hive_partitioning = true, union_by_name = true)
    WHERE user_id IS NOT NULL
),
cleared_amounts AS (
    -- Clear annualized amounts are unavailable until the privacy-approved aggregation design lands.
    SELECT
        user_id,
        CAST(NULL AS DECIMAL(18, 2)) AS annual_amount,
        CAST(NULL AS VARCHAR) AS currency
    FROM compensation_headers
    WHERE FALSE
),
risk AS (
    SELECT user_id, department_name, risk_band
    FROM read_parquet('s3://{bucket}/gold/sap_successfactors/sap_successfactors_talent_retention_risk/**/*.parquet',
                      hive_partitioning = true, union_by_name = true)
    WHERE COALESCE(invalid_score_input, TRUE) IS FALSE
      AND risk_band IN ('high', 'medium', 'low')
),
exposure AS (
    SELECT
        COALESCE(risk.department_name, 'Sin departamento') AS department_name,
        risk.risk_band,
        cleared_amounts.currency,
        cleared_amounts.user_id,
        cleared_amounts.annual_amount
    FROM cleared_amounts
    JOIN risk ON risk.user_id = cleared_amounts.user_id
    WHERE cleared_amounts.annual_amount IS NOT NULL
      AND cleared_amounts.annual_amount > 0
      AND COALESCE(cleared_amounts.currency, '') <> ''
)
SELECT
    department_name,
    risk_band,
    currency,
    COUNT(DISTINCT user_id) AS headcount,
    ROUND(SUM(annual_amount), 2) AS annualized_comp_total,
    ROUND(SUM(annual_amount) / COUNT(DISTINCT user_id), 2) AS annualized_comp_avg,
    'aggregate_min_5_per_currency' AS privacy_rule,
    'talent_attrition_exposure.v1' AS contract_version,
    CURRENT_TIMESTAMP AS generated_at
FROM exposure
GROUP BY department_name, risk_band, currency
HAVING COUNT(DISTINCT user_id) >= 5
ORDER BY department_name, risk_band, currency
