-- sap_b1_transfer_lines  (silver)  cartridge: sap_b1
-- sources: ["raw/sap_b1/OWTR", "raw/sap_b1/WTR1", "raw/sap_b1/OADM"]
-- description: Inventory transfer lines with their header (OWTR/WTR1, ObjType 67): item, quantity, source and target warehouse; stock moves, money does not.

WITH headers AS (
    SELECT * EXCLUDE (_rn)
    FROM (
        SELECT *,
               ROW_NUMBER() OVER (
                   PARTITION BY _company, DocEntry
                   ORDER BY _source_updated_at DESC NULLS LAST, load_date DESC, _extracted_at DESC
               ) AS _rn
        FROM read_parquet('s3://{bucket}/raw/sap_b1/OWTR/**/*.parquet',
                          hive_partitioning = true,
                          union_by_name   = true)
    )
    WHERE _rn = 1
),
line_versions AS (
    SELECT * EXCLUDE (_rn)
    FROM (
        SELECT *,
               ROW_NUMBER() OVER (
                   PARTITION BY _company, DocEntry, LineNum
                   ORDER BY _source_updated_at DESC NULLS LAST, load_date DESC, _extracted_at DESC
               ) AS _rn
        FROM read_parquet('s3://{bucket}/raw/sap_b1/WTR1/**/*.parquet',
                          hive_partitioning = true,
                          union_by_name   = true)
    )
    WHERE _rn = 1
),
lines AS (
    SELECT * EXCLUDE (_header_stamp)
    FROM (
        SELECT *, MAX(_source_updated_at) OVER (PARTITION BY _company, DocEntry) AS _header_stamp
        FROM line_versions
    )
    WHERE _source_updated_at IS NULL OR _source_updated_at = _header_stamp
),
company AS (
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
    h._company                                          AS company,
    CAST(h.DocEntry AS BIGINT)                          AS doc_entry,
    CAST(h.DocNum AS BIGINT)                            AS doc_num,
    CAST(l.LineNum AS BIGINT)                           AS line_num,
    CAST(h.CANCELED AS VARCHAR)                         AS canceled,
    CAST(h.DocStatus AS VARCHAR)                        AS doc_status,
    CAST(h.DocDate AS TIMESTAMP)                        AS doc_date,
    CAST(DATE_TRUNC('month', CAST(h.DocDate AS TIMESTAMP)) AS DATE) AS doc_month,
    CAST(l.ItemCode AS VARCHAR)                         AS item_code,
    CAST(l.Dscription AS VARCHAR)                       AS item_description,
    CAST(COALESCE(l.FromWhsCod, h.Filler) AS VARCHAR)   AS from_warehouse,
    CAST(COALESCE(l.WhsCode, h.ToWhsCode) AS VARCHAR)   AS to_warehouse,
    CAST(l.Quantity AS DECIMAL(19,6))                   AS quantity,
    CAST(l.StockPrice AS DECIMAL(19,6))                 AS stock_price,
    c.local_currency                                    AS local_currency,
    c.sys_currency                                      AS sys_currency,
    CAST(l.LineTotal AS DECIMAL(19,6))                  AS amount_local,
    CAST(l.TotalSumSy AS DECIMAL(19,6))                 AS amount_sys,
    CAST(h.Comments AS VARCHAR)                         AS comments,
    h._source_updated_at                                AS source_updated_at,
    h.load_date
FROM lines l
JOIN headers h ON h._company = l._company AND h.DocEntry = l.DocEntry
LEFT JOIN company c ON c._company = h._company
ORDER BY company, doc_entry, line_num
