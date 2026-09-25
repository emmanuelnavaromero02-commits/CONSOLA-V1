-- sap_b1_company  (silver)  cartridge: sap_b1
-- sources: ["raw/sap_b1/OADM", "raw/sap_b1/CINF"]
-- description: One row per company of the group: Business One company code and name, local currency, system currency, country and Business One version, from the newest OADM/CINF snapshots.

WITH oadm AS (
    SELECT * EXCLUDE (_rn)
    FROM (
        SELECT s.*, ROW_NUMBER() OVER (PARTITION BY s._company, s.Code ORDER BY s._extracted_at DESC) AS _rn
        FROM read_parquet('s3://{bucket}/raw/sap_b1/OADM/**/*.parquet',
                          hive_partitioning = true,
                          union_by_name   = true) s
        JOIN (
            SELECT _company, arg_max(regexp_replace(_run_id, '-b[0-9]+$', ''), _extracted_at) AS _run_key
            FROM read_parquet('s3://{bucket}/raw/sap_b1/OADM/**/*.parquet',
                          hive_partitioning = true,
                          union_by_name   = true)
            GROUP BY _company
        ) n ON n._company = s._company AND regexp_replace(s._run_id, '-b[0-9]+$', '') = n._run_key
    )
    WHERE _rn = 1
),
cinf AS (
    SELECT * EXCLUDE (_rn)
    FROM (
        SELECT s.*, 1 AS _rn
        FROM read_parquet('s3://{bucket}/raw/sap_b1/CINF/**/*.parquet',
                          hive_partitioning = true,
                          union_by_name   = true) s
        JOIN (
            SELECT _company, arg_max(regexp_replace(_run_id, '-b[0-9]+$', ''), _extracted_at) AS _run_key
            FROM read_parquet('s3://{bucket}/raw/sap_b1/CINF/**/*.parquet',
                          hive_partitioning = true,
                          union_by_name   = true)
            GROUP BY _company
        ) n ON n._company = s._company AND regexp_replace(s._run_id, '-b[0-9]+$', '') = n._run_key
    )
    WHERE _rn = 1
)
SELECT
    o._company                              AS company,
    CAST(o.Code AS VARCHAR)                 AS company_b1_code,
    CAST(o.CompnyName AS VARCHAR)           AS company_name,
    CAST(o.MainCurncy AS VARCHAR)           AS local_currency,
    CAST(o.SysCurrncy AS VARCHAR)           AS sys_currency,
    CAST(o.Country AS VARCHAR)              AS country,
    CAST(i.Version AS BIGINT)               AS b1_version,
    o.load_date
FROM oadm o
LEFT JOIN cinf i ON i._company = o._company
ORDER BY company
