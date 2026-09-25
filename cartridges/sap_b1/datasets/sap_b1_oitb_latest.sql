-- sap_b1_oitb_latest  (silver)  cartridge: sap_b1
-- sources: ["raw/sap_b1/OITB"]
-- description: Item groups.

WITH newest_run AS (
    SELECT _company,
           arg_max(regexp_replace(_run_id, '-b[0-9]+$', ''), _extracted_at) AS _run_key
    FROM read_parquet('s3://{bucket}/raw/sap_b1/OITB/**/*.parquet',
                      hive_partitioning = true,
                      union_by_name   = true)
    GROUP BY _company
),
latest AS (
    SELECT *
    FROM (
        SELECT s.*,
               ROW_NUMBER() OVER (PARTITION BY s._company, s.ItmsGrpCod ORDER BY s._extracted_at DESC) AS _rn
        FROM read_parquet('s3://{bucket}/raw/sap_b1/OITB/**/*.parquet',
                      hive_partitioning = true,
                      union_by_name   = true) s
        JOIN newest_run n
          ON n._company = s._company
         AND regexp_replace(s._run_id, '-b[0-9]+$', '') = n._run_key
    )
    WHERE _rn = 1
)
SELECT
    CAST(ItmsGrpCod AS BIGINT)                  AS itms_grp_cod,
    CAST(ItmsGrpNam AS VARCHAR)                 AS itms_grp_nam,
    _company                              AS company,
    load_date
FROM latest
ORDER BY company, itms_grp_cod
