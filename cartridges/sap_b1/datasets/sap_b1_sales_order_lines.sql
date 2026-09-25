-- sap_b1_sales_order_lines  (silver)  cartridge: sap_b1
-- sources: ["raw/sap_b1/ORDR", "raw/sap_b1/RDR1", "raw/sap_b1/OADM", "raw/sap_b1/IntercompanyPartners"]
-- description: sales order lines with their header (ORDR/RDR1, ObjType 17): amounts in document, local and system currency, the cost the line carries, and the intercompany flag from the configured partner mapping. CANCELED is kept (N/Y/C); gold filters it.

WITH headers AS (
    SELECT * EXCLUDE (_rn)
    FROM (
        SELECT *,
               ROW_NUMBER() OVER (
                   PARTITION BY _company, DocEntry
                   ORDER BY _source_updated_at DESC NULLS LAST, load_date DESC, _extracted_at DESC
               ) AS _rn
        FROM read_parquet('s3://{bucket}/raw/sap_b1/ORDR/**/*.parquet',
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
        FROM read_parquet('s3://{bucket}/raw/sap_b1/RDR1/**/*.parquet',
                          hive_partitioning = true,
                          union_by_name   = true)
    )
    WHERE _rn = 1
),
lines AS (
    SELECT * EXCLUDE (_header_stamp)
    FROM (
        SELECT *,
               MAX(_source_updated_at) OVER (PARTITION BY _company, DocEntry) AS _header_stamp
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
),
partners AS (
    SELECT _company, CardCode, CounterpartyCompany
    FROM (
        SELECT s.*, ROW_NUMBER() OVER (PARTITION BY s._company, s.CardCode ORDER BY s._extracted_at DESC) AS _rn
        FROM read_parquet('s3://{bucket}/raw/sap_b1/IntercompanyPartners/**/*.parquet',
                          hive_partitioning = true,
                          union_by_name   = true) s
        JOIN (
            SELECT _company, arg_max(regexp_replace(_run_id, '-b[0-9]+$', ''), _extracted_at) AS _run_key
            FROM read_parquet('s3://{bucket}/raw/sap_b1/IntercompanyPartners/**/*.parquet',
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
    CAST(h.ObjType AS VARCHAR)                          AS obj_type,
    CAST(h.DocType AS VARCHAR)                          AS doc_type,
    CAST(h.CANCELED AS VARCHAR)                         AS canceled,
    CAST(h.DocStatus AS VARCHAR)                        AS doc_status,
    CAST(l.LineStatus AS VARCHAR)                       AS line_status,
    CAST(h.DocDate AS TIMESTAMP)                        AS doc_date,
    CAST(DATE_TRUNC('month', CAST(h.DocDate AS TIMESTAMP)) AS DATE) AS doc_month,
    CAST(h.DocDueDate AS TIMESTAMP)                     AS doc_due_date,
    CAST(h.TaxDate AS TIMESTAMP)                        AS tax_date,
    CAST(h.CardCode AS VARCHAR)                         AS card_code,
    CAST(h.CardName AS VARCHAR)                         AS card_name,
    p.CardCode IS NOT NULL                              AS is_intercompany,
    CAST(p.CounterpartyCompany AS VARCHAR)              AS counterparty_company,
    CAST(h.SlpCode AS BIGINT)                           AS slp_code,
    CAST(h.BPLId AS BIGINT)                             AS branch_id,
    CAST(l.ItemCode AS VARCHAR)                         AS item_code,
    CAST(l.Dscription AS VARCHAR)                       AS item_description,
    CAST(l.WhsCode AS VARCHAR)                          AS warehouse,
    CAST(l.Quantity AS DECIMAL(19,6))                   AS quantity,
    CAST(l.OpenQty AS DECIMAL(19,6))                    AS open_qty,
    CAST(l.Price AS DECIMAL(19,6))                      AS price,
    CAST(l.PriceBefDi AS DECIMAL(19,6))                 AS price_before_discount,
    CAST(l.DiscPrcnt AS DECIMAL(19,6))                  AS discount_pct,
    COALESCE(CAST(h.DocCur AS VARCHAR), c.local_currency) AS doc_currency,
    CAST(h.DocRate AS DECIMAL(19,6))                    AS doc_rate,
    c.local_currency                                    AS local_currency,
    c.sys_currency                                      AS sys_currency,
    CASE WHEN COALESCE(CAST(h.DocCur AS VARCHAR), c.local_currency) = c.local_currency
         THEN CAST(l.LineTotal AS DECIMAL(19,6))
         ELSE CAST(l.TotalFrgn AS DECIMAL(19,6)) END    AS amount_doc,
    CAST(l.LineTotal AS DECIMAL(19,6))                  AS amount_local,
    CAST(l.TotalSumSy AS DECIMAL(19,6))                 AS amount_sys,
    CAST(l.VatSum AS DECIMAL(19,6))                     AS vat_local,
    CAST(l.VatPrcnt AS DECIMAL(19,6))                   AS vat_pct,
    CAST(l.StockPrice AS DECIMAL(19,6))                 AS stock_price,
    CAST(l.StockPrice * l.Quantity AS DECIMAL(19,6))    AS cost_local,
    CAST(l.GrssProfit AS DECIMAL(19,6))                 AS gross_profit_local,
    CAST(l.GrssProfSC AS DECIMAL(19,6))                 AS gross_profit_sys,
    CAST(l.AcctCode AS VARCHAR)                         AS account_code,
    CAST(l.OcrCode AS VARCHAR)                          AS cost_centre,
    CAST(l.BaseType AS BIGINT)                          AS base_type,
    CAST(l.BaseEntry AS BIGINT)                         AS base_entry,
    CAST(l.BaseLine AS BIGINT)                          AS base_line,
    CAST(l.TargetType AS BIGINT)                        AS target_type,
    CAST(l.TrgetEntry AS BIGINT)                        AS target_entry,
    CAST(h.TransId AS BIGINT)                           AS journal_trans_id,
    CAST(h.Comments AS VARCHAR)                         AS comments,
    h._source_updated_at                                AS source_updated_at,
    h.load_date
FROM lines l
JOIN headers h
  ON h._company = l._company AND h.DocEntry = l.DocEntry
LEFT JOIN company c
  ON c._company = h._company
LEFT JOIN partners p
  ON p._company = h._company AND p.CardCode = h.CardCode
ORDER BY company, doc_entry, line_num
