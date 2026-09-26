-- sap_b1_osri_latest  (silver)  cartridge: sap_b1
-- sources: ["raw/sap_b1/OSRI"]
-- description: Serial number master: item, internal and supplier serial, warehouse, status and dates (legacy serial table, validate in HANA).

WITH newest_run AS (
    SELECT _company,
           arg_max(regexp_replace(_run_id, '-b[0-9]+$', ''), _extracted_at) AS _run_key
    FROM read_parquet('s3://{bucket}/raw/sap_b1/OSRI/**/*.parquet',
                      hive_partitioning = true,
                      union_by_name   = true)
    GROUP BY _company
),
latest AS (
    SELECT *
    FROM (
        SELECT s.*,
               ROW_NUMBER() OVER (PARTITION BY s._company, s.ItemCode, s.SysSerial ORDER BY s._extracted_at DESC) AS _rn
        FROM read_parquet('s3://{bucket}/raw/sap_b1/OSRI/**/*.parquet',
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
    CAST(SysSerial AS BIGINT)                   AS sys_serial,
    CAST(IntrSerial AS VARCHAR)                 AS intr_serial,
    CAST(SuppSerial AS VARCHAR)                 AS supp_serial,
    CAST(WhsCode AS VARCHAR)                    AS whs_code,
    CAST(Status AS BIGINT)                      AS status,
    CAST(BatchId AS VARCHAR)                    AS batch_id,
    CAST(ExpDate AS TIMESTAMP)                  AS exp_date,
    CAST(PrdDate AS TIMESTAMP)                  AS prd_date,
    CAST(InDate AS TIMESTAMP)                   AS in_date,
    CAST(CardCode AS VARCHAR)                   AS card_code,
    CAST(CreateDate AS TIMESTAMP)               AS create_date,
    _company                              AS company,
    load_date
FROM latest
ORDER BY company, item_code, sys_serial
