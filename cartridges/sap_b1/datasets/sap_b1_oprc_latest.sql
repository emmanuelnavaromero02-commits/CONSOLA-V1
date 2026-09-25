-- sap_b1_oprc_latest  (silver)  cartridge: sap_b1
-- sources: ["raw/sap_b1/OPRC"]
-- description: Profit centres / cost centres (OcrCode dimension).

WITH newest_run AS (
    SELECT _company,
           arg_max(regexp_replace(_run_id, '-b[0-9]+$', ''), _extracted_at) AS _run_key
    FROM read_parquet('s3://{bucket}/raw/sap_b1/OPRC/**/*.parquet',
                      hive_partitioning = true,
                      union_by_name   = true)
    GROUP BY _company
),
latest AS (
    SELECT *
    FROM (
        SELECT s.*,
               ROW_NUMBER() OVER (PARTITION BY s._company, s.PrcCode ORDER BY s._extracted_at DESC) AS _rn
        FROM read_parquet('s3://{bucket}/raw/sap_b1/OPRC/**/*.parquet',
                      hive_partitioning = true,
                      union_by_name   = true) s
        JOIN newest_run n
          ON n._company = s._company
         AND regexp_replace(s._run_id, '-b[0-9]+$', '') = n._run_key
    )
    WHERE _rn = 1
)
SELECT
    CAST(PrcCode AS VARCHAR)                    AS prc_code,
    CAST(PrcName AS VARCHAR)                    AS prc_name,
    CAST(DimCode AS BIGINT)                     AS dim_code,
    CAST(Active AS VARCHAR)                     AS active,
    _company                              AS company,
    load_date
FROM latest
ORDER BY company, prc_code
