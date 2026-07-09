-- sap_successfactors_talent_tenure_by_cohort_month  (gold)  cartridge: sap_successfactors
-- sources: ["gold/sap_successfactors/sap_successfactors_employee_360"]
-- description: Serie de tiempo mensual de antiguedad promedio (meses) de la plantilla activa por cohorte organizacional (division), derivada de start_date/end_date. Una fila por (cohorte, mes) para los ultimos 36 meses. Habilita senales de tendencia e intel Monte Carlo (derived) con historia real.

WITH emp AS (
    SELECT
        COALESCE(CAST(division_id AS VARCHAR), '(sin division)') AS cohort_id,
        COALESCE(division_name, '(sin division)')                AS cohort_name,
        CAST(start_date AS DATE)                                 AS sd,
        CASE WHEN end_date IS NULL THEN NULL ELSE CAST(end_date AS DATE) END AS ed
    FROM read_parquet('s3://{bucket}/gold/sap_successfactors/sap_successfactors_employee_360/**/*.parquet',
                      hive_partitioning = true,
                      union_by_name = true)
    WHERE start_date IS NOT NULL
),
divs AS (
    SELECT cohort_id, MAX(cohort_name) AS cohort_name FROM emp GROUP BY cohort_id
),
months AS (
    SELECT CAST(gs AS DATE) AS snapshot_month
    FROM generate_series(
        CAST(DATE_TRUNC('month', CURRENT_DATE) AS TIMESTAMP) - INTERVAL 35 MONTH,
        CAST(DATE_TRUNC('month', CURRENT_DATE) AS TIMESTAMP),
        INTERVAL 1 MONTH
    ) AS t(gs)
),
grid AS (
    SELECT d.cohort_id, d.cohort_name, m.snapshot_month
    FROM divs d CROSS JOIN months m
)
SELECT
    g.cohort_id                                       AS cohort_id,
    g.cohort_name                                     AS cohort_name,
    'division'                                        AS cohort_kind,
    g.snapshot_month                                  AS snapshot_month,
    -- Antiguedad (meses) de cada empleado activo AL MES g.snapshot_month; promedio por cohorte.
    ROUND(AVG(DATE_DIFF('month', e.sd, g.snapshot_month)), 2) AS avg_tenure_months,
    COUNT(e.sd)                                       AS cohort_size,
    CURRENT_TIMESTAMP                                 AS generated_at
FROM grid g
LEFT JOIN emp e
    ON  e.cohort_id IS NOT DISTINCT FROM g.cohort_id
    AND e.sd <= (g.snapshot_month + INTERVAL 1 MONTH - INTERVAL 1 DAY)
    AND (e.ed IS NULL OR e.ed >= g.snapshot_month)
GROUP BY 1, 2, 3, 4
ORDER BY cohort_id, snapshot_month
