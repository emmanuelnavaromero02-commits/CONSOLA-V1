-- sap_b1_inventory_movements  (silver)  cartridge: sap_b1
-- sources: ["raw/sap_b1/OINM", "raw/sap_b1/OITM", "raw/sap_b1/OADM"]
-- description: Every stock movement per company (OINM, immutable), with the item's name and group and the company's currencies; net quantity and value per row.

WITH movements AS (
    SELECT * EXCLUDE (_rn)
    FROM (
        SELECT *,
               ROW_NUMBER() OVER (
                   PARTITION BY _company, TransNum, TransSeq
                   ORDER BY load_date DESC, _extracted_at DESC
               ) AS _rn
        FROM read_parquet('s3://{bucket}/raw/sap_b1/OINM/**/*.parquet',
                          hive_partitioning = true,
                          union_by_name   = true)
    )
    WHERE _rn = 1
),
items AS (
    SELECT * EXCLUDE (_rn)
    FROM (
        SELECT *,
               ROW_NUMBER() OVER (
                   PARTITION BY _company, ItemCode
                   ORDER BY _source_updated_at DESC NULLS LAST, load_date DESC, _extracted_at DESC
               ) AS _rn
        FROM read_parquet('s3://{bucket}/raw/sap_b1/OITM/**/*.parquet',
                          hive_partitioning = true,
                          union_by_name   = true)
    )
    WHERE _rn = 1
),
company AS (
    -- Newest run per company (all of its batches), never a mix of two runs.
    SELECT _company, MainCurncy AS local_currency, SysCurrncy AS sys_currency
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
)
SELECT
    m._company                                          AS company,
    CAST(m.TransNum AS BIGINT)                          AS trans_num,
    CAST(m.TransSeq AS BIGINT)                          AS trans_seq,
    CAST(m.DocDate AS TIMESTAMP)                        AS doc_date,
    CAST(DATE_TRUNC('month', CAST(m.DocDate AS TIMESTAMP)) AS DATE) AS doc_month,
    CAST(m.ItemCode AS VARCHAR)                         AS item_code,
    CAST(i.ItemName AS VARCHAR)                         AS item_name,
    CAST(i.ItmsGrpCod AS BIGINT)                        AS item_group_code,
    CAST(m.Warehouse AS VARCHAR)                        AS warehouse,
    CAST(m.InQty AS DECIMAL(19,6))                      AS in_qty,
    CAST(m.OutQty AS DECIMAL(19,6))                     AS out_qty,
    CAST(m.InQty - m.OutQty AS DECIMAL(19,6))           AS net_qty,
    CAST(m.Price AS DECIMAL(19,6))                      AS price,
    CAST(m.CalcPrice AS DECIMAL(19,6))                  AS calc_price,
    CAST(m.TransValue AS DECIMAL(19,6))                 AS trans_value_local,
    CAST(m.Currency AS VARCHAR)                         AS movement_currency,
    c.local_currency                                    AS local_currency,
    c.sys_currency                                      AS sys_currency,
    CAST(m.TransType AS BIGINT)                         AS trans_type,
    CAST(m.CreatedBy AS BIGINT)                         AS created_by_doc_entry,
    CAST(m.BASE_REF AS VARCHAR)                         AS base_ref,
    CAST(m.DocLineNum AS BIGINT)                        AS doc_line_num,
    CAST(m.ApplObj AS VARCHAR)                          AS appl_obj,
    CAST(m.AppObjAbs AS BIGINT)                         AS appl_obj_abs,
    CAST(m.CreateDate AS TIMESTAMP)                     AS created_at,
    m.load_date
FROM movements m
LEFT JOIN items i ON i._company = m._company AND i.ItemCode = m.ItemCode
LEFT JOIN company c ON c._company = m._company
ORDER BY company, trans_num, trans_seq
