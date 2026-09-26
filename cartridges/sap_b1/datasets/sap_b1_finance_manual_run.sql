-- sap_b1_finance_manual_run  (silver)  cartridge: sap_b1
-- sources: ["raw/sap_b1/FinanceManualRun"]
-- description: Finance's manual run of the margin indicators as uploaded from the console, one row per indicator, company (or grupo), month, dimension and key, value and unit (monto in the company's local currency, pct in percent or percentage points); for each company and month only the newest upload counts, so uploading a month again replaces it.

WITH runs AS (
    SELECT *, regexp_replace(_run_id, '-b[0-9]+$', '') AS _run_key
    FROM read_parquet('s3://{bucket}/raw/sap_b1/FinanceManualRun/**/*.parquet',
                      hive_partitioning = true,
                      union_by_name   = true)
),
newest AS (
    SELECT Company, Period, arg_max(_run_key, _extracted_at) AS _run_key
    FROM runs
    GROUP BY 1, 2
)
SELECT
    CAST(r.Company AS VARCHAR)                          AS company,
    CAST(r.Period AS VARCHAR)                           AS period,
    CAST(CAST(r.Period AS VARCHAR) || '-01' AS DATE)    AS doc_month,
    CAST(r.Indicator AS VARCHAR)                        AS indicator,
    CAST(r.Dimension AS VARCHAR)                        AS dimension,
    COALESCE(CAST(r.DimKey AS VARCHAR), '')             AS dim_key,
    CAST(r.Unit AS VARCHAR)                             AS unit,
    CAST(r.ValueNum AS DECIMAL(19,6))                   AS finance_value,
    r._run_key                                          AS upload_id,
    CAST(r._extracted_at AS TIMESTAMP)                  AS uploaded_at
FROM runs r
JOIN newest n
  ON n.Company = r.Company AND n.Period = r.Period AND n._run_key = r._run_key
ORDER BY company, period, indicator, dimension, dim_key
