-- sap_b1_sri1_latest  (silver)  cartridge: sap_b1
-- sources: ["raw/sap_b1/SRI1"]
-- description: Serial numbers per document line, Direction 0 in / 1 out (legacy serial table, validate in HANA).

WITH newest_run AS (
    SELECT _company,
           arg_max(regexp_replace(_run_id, '-b[0-9]+$', ''), _extracted_at) AS _run_key
    FROM read_parquet('s3://{bucket}/raw/sap_b1/SRI1/**/*.parquet',
                      hive_partitioning = true,
                      union_by_name   = true)
    GROUP BY _company
),
latest AS (
    SELECT *
    FROM (
        SELECT s.*,
               ROW_NUMBER() OVER (PARTITION BY s._company, s.ItemCode, s.SysSerial, s.LineNum ORDER BY s._extracted_at DESC) AS _rn
        FROM read_parquet('s3://{bucket}/raw/sap_b1/SRI1/**/*.parquet',
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
    CAST(LineNum AS BIGINT)                     AS line_num,
    CAST(BaseType AS BIGINT)                    AS base_type,
    CAST(BaseEntry AS BIGINT)                   AS base_entry,
    CAST(BaseNum AS BIGINT)                     AS base_num,
    CAST(BaseLinNum AS BIGINT)                  AS base_lin_num,
    CAST(DocDate AS TIMESTAMP)                  AS doc_date,
    CAST(CardCode AS VARCHAR)                   AS card_code,
    CAST(WhsCode AS VARCHAR)                    AS whs_code,
    CAST(Direction AS BIGINT)                   AS direction,
    _company                              AS company,
    load_date
FROM latest
ORDER BY company, item_code, sys_serial, line_num
