-- sap_b1_business_parameters  (silver)  cartridge: sap_b1
-- sources: ["raw/sap_b1/BusinessParameters"]
-- description: The business parameters of the workspace from the latest snapshot: finance control totals per company and month, revenue and cost-of-sales accounts, thresholds and settings; company * applies to every company and period * to every month.

WITH snapshots AS (
    SELECT *
    FROM read_parquet('s3://{bucket}/raw/sap_b1/BusinessParameters/**/*.parquet',
                      hive_partitioning = true,
                      union_by_name   = true)
),
latest AS (
    SELECT arg_max(regexp_replace(_run_id, '-b[0-9]+$', ''), _extracted_at) AS _run_key
    FROM snapshots
)
SELECT
    CAST(s.Kind AS VARCHAR)                 AS kind,
    CAST(s.Company AS VARCHAR)              AS company,
    CAST(s.Period AS VARCHAR)               AS period,
    CAST(s.ParamKey AS VARCHAR)             AS param_key,
    CAST(s.ValueText AS VARCHAR)            AS value_text,
    CAST(s.ValueNum AS DECIMAL(19,6))       AS value_num,
    CAST(s._extracted_at AS VARCHAR)        AS loaded_at
FROM snapshots s
JOIN latest l ON regexp_replace(s._run_id, '-b[0-9]+$', '') = l._run_key
ORDER BY kind, company, period, param_key
