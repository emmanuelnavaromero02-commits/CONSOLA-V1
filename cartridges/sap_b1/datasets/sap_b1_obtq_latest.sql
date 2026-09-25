-- sap_b1_obtq_latest  (silver)  cartridge: sap_b1
-- sources: ["raw/sap_b1/OBTQ"]
-- description: Batch quantity per item, batch (SysNumber) and warehouse (snapshot).

WITH newest_run AS (
    SELECT _company,
           arg_max(regexp_replace(_run_id, '-b[0-9]+$', ''), _extracted_at) AS _run_key
    FROM read_parquet('s3://{bucket}/raw/sap_b1/OBTQ/**/*.parquet',
                      hive_partitioning = true,
                      union_by_name   = true)
    GROUP BY _company
),
latest AS (
    SELECT *
    FROM (
        SELECT s.*,
               ROW_NUMBER() OVER (PARTITION BY s._company, s.ItemCode, s.SysNumber, s.WhsCode ORDER BY s._extracted_at DESC) AS _rn
        FROM read_parquet('s3://{bucket}/raw/sap_b1/OBTQ/**/*.parquet',
                      hive_partitioning = true,
                      union_by_name   = true) s
        JOIN newest_run n
          ON n._company = s._company
         AND regexp_replace(s._run_id, '-b[0-9]+$', '') = n._run_key
    )
    WHERE _rn = 1
)
SELECT
    CAST(ItemCode AS VARCHAR)                   AS item_code,
    CAST(SysNumber AS BIGINT)                   AS sys_number,
    CAST(WhsCode AS VARCHAR)                    AS whs_code,
    CAST(Quantity AS DECIMAL(19,6))             AS quantity,
    _company                              AS company,
    load_date
FROM latest
ORDER BY company, item_code, sys_number, whs_code
