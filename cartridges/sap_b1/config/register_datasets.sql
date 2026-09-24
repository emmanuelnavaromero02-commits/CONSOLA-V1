-- register_datasets.sql — GENERATED from cartridges/sap_b1/datasets/*.sql by
-- tools/generate_register_datasets.py; a test keeps them equal. Do not edit.
--
-- Registers the SAP Business One datasets in the workspace of the customer
-- group. Run once the workspace exists, from infra/terraform/deploy on the host:
--   docker compose -f docker-compose.aws.yml exec -T postgres psql -U postgres -d modecissions \
--     -v workspace_id='<uuid>' -v tenant_id='<uuid>' -f /docker-entrypoint-initdb.d/../cartridges/sap_b1/config/register_datasets.sql
-- (or copy the file in). Idempotent: re-running updates the SQL to match the files.
-- 62 silver + 6 gold datasets.

\set ON_ERROR_STOP on
SELECT :'workspace_id'::uuid AS workspace_id, :'tenant_id'::uuid AS tenant_id;

INSERT INTO datasets (name, layer, cartridge, sources, sql_def, description, column_mapping, schedule, updated_at, workspace_id)
VALUES
($seed$sap_b1_ap_credit_memo_lines$seed$, $seed$silver$seed$, $seed$sap_b1$seed$, $seed$["raw/sap_b1/ORPC", "raw/sap_b1/RPC1", "raw/sap_b1/OADM", "raw/sap_b1/IntercompanyPartners"]$seed$::jsonb, $seed$
-- sap_b1_ap_credit_memo_lines  (silver)  cartridge: sap_b1
-- sources: ["raw/sap_b1/ORPC", "raw/sap_b1/RPC1", "raw/sap_b1/OADM", "raw/sap_b1/IntercompanyPartners"]
-- description: A/P credit memo lines with their header (ORPC/RPC1, ObjType 19): amounts in document, local and system currency, the cost the line carries, and the intercompany flag from the configured partner mapping. CANCELED is kept (N/Y/C); gold filters it.

WITH headers AS (
    -- Current version of every header per company over the whole bronze history.
    SELECT * EXCLUDE (_rn)
    FROM (
        SELECT *,
               ROW_NUMBER() OVER (
                   PARTITION BY _company, DocEntry
                   ORDER BY _source_updated_at DESC NULLS LAST, load_date DESC, _extracted_at DESC
               ) AS _rn
        FROM read_parquet('s3://{bucket}/raw/sap_b1/ORPC/**/*.parquet',
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
        FROM read_parquet('s3://{bucket}/raw/sap_b1/RPC1/**/*.parquet',
                          hive_partitioning = true,
                          union_by_name   = true)
    )
    WHERE _rn = 1
),
lines AS (
    -- Only the lines that carry the header's latest stamp: a line dropped from
    -- the document disappears the moment the document is re-read.
    SELECT * EXCLUDE (_header_stamp)
    FROM (
        SELECT *,
               MAX(_source_updated_at) OVER (PARTITION BY _company, DocEntry) AS _header_stamp
        FROM line_versions
    )
    WHERE _source_updated_at IS NULL OR _source_updated_at = _header_stamp
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
),
partners AS (
    -- Newest run per company (all of its batches), never a mix of two runs.
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
    -- Currency, explicit on every row: the document's, the company's local
    -- and the company's system currency. DocRate is 0 on a local-currency
    -- document, as Business One stores it.
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
    -- The cost the line carries: Business One's stock price at posting time
    -- times the quantity, and its own gross profit figures.
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
$seed$, $seed$A/P credit memo lines with their header (ORPC/RPC1, ObjType 19): amounts in document, local and system currency, the cost the line carries, and the intercompany flag from the configured partner mapping. CANCELED is kept (N/Y/C); gold filters it.$seed$, $seed${}$seed$::jsonb, $seed$$seed$, NOW(), :'workspace_id'::uuid),
($seed$sap_b1_ap_invoice_lines$seed$, $seed$silver$seed$, $seed$sap_b1$seed$, $seed$["raw/sap_b1/OPCH", "raw/sap_b1/PCH1", "raw/sap_b1/OADM", "raw/sap_b1/IntercompanyPartners"]$seed$::jsonb, $seed$
-- sap_b1_ap_invoice_lines  (silver)  cartridge: sap_b1
-- sources: ["raw/sap_b1/OPCH", "raw/sap_b1/PCH1", "raw/sap_b1/OADM", "raw/sap_b1/IntercompanyPartners"]
-- description: A/P invoice lines with their header (OPCH/PCH1, ObjType 18): amounts in document, local and system currency, the cost the line carries, and the intercompany flag from the configured partner mapping. CANCELED is kept (N/Y/C); gold filters it.

WITH headers AS (
    -- Current version of every header per company over the whole bronze history.
    SELECT * EXCLUDE (_rn)
    FROM (
        SELECT *,
               ROW_NUMBER() OVER (
                   PARTITION BY _company, DocEntry
                   ORDER BY _source_updated_at DESC NULLS LAST, load_date DESC, _extracted_at DESC
               ) AS _rn
        FROM read_parquet('s3://{bucket}/raw/sap_b1/OPCH/**/*.parquet',
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
        FROM read_parquet('s3://{bucket}/raw/sap_b1/PCH1/**/*.parquet',
                          hive_partitioning = true,
                          union_by_name   = true)
    )
    WHERE _rn = 1
),
lines AS (
    -- Only the lines that carry the header's latest stamp: a line dropped from
    -- the document disappears the moment the document is re-read.
    SELECT * EXCLUDE (_header_stamp)
    FROM (
        SELECT *,
               MAX(_source_updated_at) OVER (PARTITION BY _company, DocEntry) AS _header_stamp
        FROM line_versions
    )
    WHERE _source_updated_at IS NULL OR _source_updated_at = _header_stamp
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
),
partners AS (
    -- Newest run per company (all of its batches), never a mix of two runs.
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
    -- Currency, explicit on every row: the document's, the company's local
    -- and the company's system currency. DocRate is 0 on a local-currency
    -- document, as Business One stores it.
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
    -- The cost the line carries: Business One's stock price at posting time
    -- times the quantity, and its own gross profit figures.
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
$seed$, $seed$A/P invoice lines with their header (OPCH/PCH1, ObjType 18): amounts in document, local and system currency, the cost the line carries, and the intercompany flag from the configured partner mapping. CANCELED is kept (N/Y/C); gold filters it.$seed$, $seed${}$seed$::jsonb, $seed$$seed$, NOW(), :'workspace_id'::uuid),
($seed$sap_b1_ar_credit_memo_lines$seed$, $seed$silver$seed$, $seed$sap_b1$seed$, $seed$["raw/sap_b1/ORIN", "raw/sap_b1/RIN1", "raw/sap_b1/OADM", "raw/sap_b1/IntercompanyPartners"]$seed$::jsonb, $seed$
-- sap_b1_ar_credit_memo_lines  (silver)  cartridge: sap_b1
-- sources: ["raw/sap_b1/ORIN", "raw/sap_b1/RIN1", "raw/sap_b1/OADM", "raw/sap_b1/IntercompanyPartners"]
-- description: A/R credit memo lines with their header (ORIN/RIN1, ObjType 14): amounts in document, local and system currency, the cost the line carries, and the intercompany flag from the configured partner mapping. CANCELED is kept (N/Y/C); gold filters it.

WITH headers AS (
    -- Current version of every header per company over the whole bronze history.
    SELECT * EXCLUDE (_rn)
    FROM (
        SELECT *,
               ROW_NUMBER() OVER (
                   PARTITION BY _company, DocEntry
                   ORDER BY _source_updated_at DESC NULLS LAST, load_date DESC, _extracted_at DESC
               ) AS _rn
        FROM read_parquet('s3://{bucket}/raw/sap_b1/ORIN/**/*.parquet',
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
        FROM read_parquet('s3://{bucket}/raw/sap_b1/RIN1/**/*.parquet',
                          hive_partitioning = true,
                          union_by_name   = true)
    )
    WHERE _rn = 1
),
lines AS (
    -- Only the lines that carry the header's latest stamp: a line dropped from
    -- the document disappears the moment the document is re-read.
    SELECT * EXCLUDE (_header_stamp)
    FROM (
        SELECT *,
               MAX(_source_updated_at) OVER (PARTITION BY _company, DocEntry) AS _header_stamp
        FROM line_versions
    )
    WHERE _source_updated_at IS NULL OR _source_updated_at = _header_stamp
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
),
partners AS (
    -- Newest run per company (all of its batches), never a mix of two runs.
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
    -- Currency, explicit on every row: the document's, the company's local
    -- and the company's system currency. DocRate is 0 on a local-currency
    -- document, as Business One stores it.
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
    -- The cost the line carries: Business One's stock price at posting time
    -- times the quantity, and its own gross profit figures.
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
$seed$, $seed$A/R credit memo lines with their header (ORIN/RIN1, ObjType 14): amounts in document, local and system currency, the cost the line carries, and the intercompany flag from the configured partner mapping. CANCELED is kept (N/Y/C); gold filters it.$seed$, $seed${}$seed$::jsonb, $seed$$seed$, NOW(), :'workspace_id'::uuid),
($seed$sap_b1_ar_invoice_lines$seed$, $seed$silver$seed$, $seed$sap_b1$seed$, $seed$["raw/sap_b1/OINV", "raw/sap_b1/INV1", "raw/sap_b1/OADM", "raw/sap_b1/IntercompanyPartners"]$seed$::jsonb, $seed$
-- sap_b1_ar_invoice_lines  (silver)  cartridge: sap_b1
-- sources: ["raw/sap_b1/OINV", "raw/sap_b1/INV1", "raw/sap_b1/OADM", "raw/sap_b1/IntercompanyPartners"]
-- description: A/R invoice lines with their header (OINV/INV1, ObjType 13): amounts in document, local and system currency, the cost the line carries, and the intercompany flag from the configured partner mapping. CANCELED is kept (N/Y/C); gold filters it.

WITH headers AS (
    -- Current version of every header per company over the whole bronze history.
    SELECT * EXCLUDE (_rn)
    FROM (
        SELECT *,
               ROW_NUMBER() OVER (
                   PARTITION BY _company, DocEntry
                   ORDER BY _source_updated_at DESC NULLS LAST, load_date DESC, _extracted_at DESC
               ) AS _rn
        FROM read_parquet('s3://{bucket}/raw/sap_b1/OINV/**/*.parquet',
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
        FROM read_parquet('s3://{bucket}/raw/sap_b1/INV1/**/*.parquet',
                          hive_partitioning = true,
                          union_by_name   = true)
    )
    WHERE _rn = 1
),
lines AS (
    -- Only the lines that carry the header's latest stamp: a line dropped from
    -- the document disappears the moment the document is re-read.
    SELECT * EXCLUDE (_header_stamp)
    FROM (
        SELECT *,
               MAX(_source_updated_at) OVER (PARTITION BY _company, DocEntry) AS _header_stamp
        FROM line_versions
    )
    WHERE _source_updated_at IS NULL OR _source_updated_at = _header_stamp
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
),
partners AS (
    -- Newest run per company (all of its batches), never a mix of two runs.
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
    -- Currency, explicit on every row: the document's, the company's local
    -- and the company's system currency. DocRate is 0 on a local-currency
    -- document, as Business One stores it.
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
    -- The cost the line carries: Business One's stock price at posting time
    -- times the quantity, and its own gross profit figures.
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
$seed$, $seed$A/R invoice lines with their header (OINV/INV1, ObjType 13): amounts in document, local and system currency, the cost the line carries, and the intercompany flag from the configured partner mapping. CANCELED is kept (N/Y/C); gold filters it.$seed$, $seed${}$seed$::jsonb, $seed$$seed$, NOW(), :'workspace_id'::uuid),
($seed$sap_b1_business_partners$seed$, $seed$silver$seed$, $seed$sap_b1$seed$, $seed$["raw/sap_b1/OCRD", "raw/sap_b1/OCRG", "raw/sap_b1/IntercompanyPartners"]$seed$::jsonb, $seed$
-- sap_b1_business_partners  (silver)  cartridge: sap_b1
-- sources: ["raw/sap_b1/OCRD", "raw/sap_b1/OCRG", "raw/sap_b1/IntercompanyPartners"]
-- description: Current business partners per company (customers C, suppliers S) with their group name and the intercompany flag from the configured partner mapping.

WITH ocrd AS (
    SELECT * EXCLUDE (_rn)
    FROM (
        SELECT *,
               ROW_NUMBER() OVER (
                   PARTITION BY _company, CardCode
                   ORDER BY _source_updated_at DESC NULLS LAST, load_date DESC, _extracted_at DESC
               ) AS _rn
        FROM read_parquet('s3://{bucket}/raw/sap_b1/OCRD/**/*.parquet',
                          hive_partitioning = true,
                          union_by_name   = true)
    )
    WHERE _rn = 1
),
groups AS (
    -- Newest run per company (all of its batches), never a mix of two runs.
    SELECT * EXCLUDE (_rn)
    FROM (
        SELECT s.*, ROW_NUMBER() OVER (PARTITION BY s._company, s.GroupCode ORDER BY s._extracted_at DESC) AS _rn
        FROM read_parquet('s3://{bucket}/raw/sap_b1/OCRG/**/*.parquet',
                          hive_partitioning = true,
                          union_by_name   = true) s
        JOIN (
            SELECT _company, arg_max(regexp_replace(_run_id, '-b[0-9]+$', ''), _extracted_at) AS _run_key
            FROM read_parquet('s3://{bucket}/raw/sap_b1/OCRG/**/*.parquet',
                          hive_partitioning = true,
                          union_by_name   = true)
            GROUP BY _company
        ) n ON n._company = s._company AND regexp_replace(s._run_id, '-b[0-9]+$', '') = n._run_key
    )
    WHERE _rn = 1
),
partners AS (
    -- Newest run per company (all of its batches), never a mix of two runs.
    SELECT _company, CardCode, CounterpartyCompany, MappingSource
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
    b._company                              AS company,
    CAST(b.CardCode AS VARCHAR)             AS card_code,
    CAST(b.CardName AS VARCHAR)             AS card_name,
    CAST(b.CardType AS VARCHAR)             AS card_type,
    CAST(b.GroupCode AS BIGINT)             AS group_code,
    CAST(g.GroupName AS VARCHAR)            AS group_name,
    CAST(b.Currency AS VARCHAR)             AS partner_currency,
    CAST(b.SlpCode AS BIGINT)               AS slp_code,
    CAST(b.Country AS VARCHAR)              AS country,
    CAST(b.validFor AS VARCHAR)             AS valid_for,
    CAST(b.frozenFor AS VARCHAR)            AS frozen_for,
    p.CardCode IS NOT NULL                  AS is_intercompany,
    CAST(p.CounterpartyCompany AS VARCHAR)  AS counterparty_company,
    CAST(p.MappingSource AS VARCHAR)        AS mapping_source,
    CAST(b.CreateDate AS TIMESTAMP)         AS created_at,
    b._source_updated_at                    AS source_updated_at,
    b.load_date
FROM ocrd b
LEFT JOIN groups g ON g._company = b._company AND g.GroupCode = b.GroupCode
LEFT JOIN partners p ON p._company = b._company AND p.CardCode = b.CardCode
ORDER BY company, card_code
$seed$, $seed$Current business partners per company (customers C, suppliers S) with their group name and the intercompany flag from the configured partner mapping.$seed$, $seed${}$seed$::jsonb, $seed$$seed$, NOW(), :'workspace_id'::uuid),
($seed$sap_b1_cinf_latest$seed$, $seed$silver$seed$, $seed$sap_b1$seed$, $seed$["raw/sap_b1/CINF"]$seed$::jsonb, $seed$
-- sap_b1_cinf_latest  (silver)  cartridge: sap_b1
-- sources: ["raw/sap_b1/CINF"]
-- description: Company database version and name (one row per company).

WITH newest_run AS (
    -- Instantánea sin marca de agua: la corrida más reciente POR EMPRESA
    -- (todos sus lotes) y nada más. Deduplicar el histórico resucitaría
    -- filas que la fuente borró; quedarse con MAX(load_date) mezclaría dos
    -- corridas del mismo día.
    SELECT _company,
           arg_max(regexp_replace(_run_id, '-b[0-9]+$', ''), _extracted_at) AS _run_key
    FROM read_parquet('s3://{bucket}/raw/sap_b1/CINF/**/*.parquet',
                      hive_partitioning = true,
                      union_by_name   = true)
    GROUP BY _company
),
latest AS (
    SELECT *
    FROM (
        SELECT s.*,
               1 AS _rn
        FROM read_parquet('s3://{bucket}/raw/sap_b1/CINF/**/*.parquet',
                      hive_partitioning = true,
                      union_by_name   = true) s
        JOIN newest_run n
          ON n._company = s._company
         AND regexp_replace(s._run_id, '-b[0-9]+$', '') = n._run_key
    )
    WHERE _rn = 1
)
SELECT
    CAST(Version AS BIGINT)                     AS version,
    CAST(CompnyName AS VARCHAR)                 AS compny_name,
    _company                              AS company,
    load_date
FROM latest
ORDER BY company
$seed$, $seed$Company database version and name (one row per company).$seed$, $seed${}$seed$::jsonb, $seed$$seed$, NOW(), :'workspace_id'::uuid),
($seed$sap_b1_company$seed$, $seed$silver$seed$, $seed$sap_b1$seed$, $seed$["raw/sap_b1/OADM", "raw/sap_b1/CINF"]$seed$::jsonb, $seed$
-- sap_b1_company  (silver)  cartridge: sap_b1
-- sources: ["raw/sap_b1/OADM", "raw/sap_b1/CINF"]
-- description: One row per company of the group: Business One company code and name, local currency, system currency, country and Business One version, from the newest OADM/CINF snapshots.

WITH oadm AS (
    -- Newest run per company (all of its batches), never a mix of two runs.
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
    -- Newest run per company (all of its batches), never a mix of two runs.
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
$seed$, $seed$One row per company of the group: Business One company code and name, local currency, system currency, country and Business One version, from the newest OADM/CINF snapshots.$seed$, $seed${}$seed$::jsonb, $seed$$seed$, NOW(), :'workspace_id'::uuid),
($seed$sap_b1_delivery_lines$seed$, $seed$silver$seed$, $seed$sap_b1$seed$, $seed$["raw/sap_b1/ODLN", "raw/sap_b1/DLN1", "raw/sap_b1/OADM", "raw/sap_b1/IntercompanyPartners"]$seed$::jsonb, $seed$
-- sap_b1_delivery_lines  (silver)  cartridge: sap_b1
-- sources: ["raw/sap_b1/ODLN", "raw/sap_b1/DLN1", "raw/sap_b1/OADM", "raw/sap_b1/IntercompanyPartners"]
-- description: delivery lines with their header (ODLN/DLN1, ObjType 15): amounts in document, local and system currency, the cost the line carries, and the intercompany flag from the configured partner mapping. CANCELED is kept (N/Y/C); gold filters it.

WITH headers AS (
    -- Current version of every header per company over the whole bronze history.
    SELECT * EXCLUDE (_rn)
    FROM (
        SELECT *,
               ROW_NUMBER() OVER (
                   PARTITION BY _company, DocEntry
                   ORDER BY _source_updated_at DESC NULLS LAST, load_date DESC, _extracted_at DESC
               ) AS _rn
        FROM read_parquet('s3://{bucket}/raw/sap_b1/ODLN/**/*.parquet',
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
        FROM read_parquet('s3://{bucket}/raw/sap_b1/DLN1/**/*.parquet',
                          hive_partitioning = true,
                          union_by_name   = true)
    )
    WHERE _rn = 1
),
lines AS (
    -- Only the lines that carry the header's latest stamp: a line dropped from
    -- the document disappears the moment the document is re-read.
    SELECT * EXCLUDE (_header_stamp)
    FROM (
        SELECT *,
               MAX(_source_updated_at) OVER (PARTITION BY _company, DocEntry) AS _header_stamp
        FROM line_versions
    )
    WHERE _source_updated_at IS NULL OR _source_updated_at = _header_stamp
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
),
partners AS (
    -- Newest run per company (all of its batches), never a mix of two runs.
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
    -- Currency, explicit on every row: the document's, the company's local
    -- and the company's system currency. DocRate is 0 on a local-currency
    -- document, as Business One stores it.
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
    -- The cost the line carries: Business One's stock price at posting time
    -- times the quantity, and its own gross profit figures.
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
$seed$, $seed$delivery lines with their header (ODLN/DLN1, ObjType 15): amounts in document, local and system currency, the cost the line carries, and the intercompany flag from the configured partner mapping. CANCELED is kept (N/Y/C); gold filters it.$seed$, $seed${}$seed$::jsonb, $seed$$seed$, NOW(), :'workspace_id'::uuid),
($seed$sap_b1_dln1_latest$seed$, $seed$silver$seed$, $seed$sap_b1$seed$, $seed$["raw/sap_b1/DLN1"]$seed$::jsonb, $seed$
-- sap_b1_dln1_latest  (silver)  cartridge: sap_b1
-- sources: ["raw/sap_b1/DLN1"]
-- description: Deliveries lines, read through ODLN: quantities, prices, line totals in three currencies, base/target links.

WITH versions AS (
    -- Estado ACTUAL por clave sobre TODO el histórico bronze; después solo
    -- las líneas que llevan la marca más reciente de su cabecera: una línea
    -- borrada del documento desaparece en cuanto la cabecera se relee.
    SELECT *
    FROM (
        SELECT *,
               ROW_NUMBER() OVER (
                   PARTITION BY _company, DocEntry, LineNum
                   ORDER BY _source_updated_at DESC NULLS LAST, load_date DESC, _extracted_at DESC
               ) AS _rn
        FROM read_parquet('s3://{bucket}/raw/sap_b1/DLN1/**/*.parquet',
                      hive_partitioning = true,
                      union_by_name   = true)
    )
    WHERE _rn = 1
),
current_lines AS (
    SELECT *
    FROM (
        SELECT *,
               MAX(_source_updated_at) OVER (PARTITION BY _company, DocEntry) AS _header_stamp
        FROM versions
    )
    WHERE _source_updated_at IS NULL OR _source_updated_at = _header_stamp
)
SELECT
    CAST(DocEntry AS BIGINT)                    AS doc_entry,
    CAST(LineNum AS BIGINT)                     AS line_num,
    CAST(TargetType AS BIGINT)                  AS target_type,
    CAST(TrgetEntry AS BIGINT)                  AS trget_entry,
    CAST(BaseType AS BIGINT)                    AS base_type,
    CAST(BaseEntry AS BIGINT)                   AS base_entry,
    CAST(BaseLine AS BIGINT)                    AS base_line,
    CAST(LineStatus AS VARCHAR)                 AS line_status,
    CAST(ItemCode AS VARCHAR)                   AS item_code,
    CAST(Dscription AS VARCHAR)                 AS dscription,
    CAST(Quantity AS DECIMAL(19,6))             AS quantity,
    CAST(OpenQty AS DECIMAL(19,6))              AS open_qty,
    CAST(Price AS DECIMAL(19,6))                AS price,
    CAST(PriceBefDi AS DECIMAL(19,6))           AS price_bef_di,
    CAST(Currency AS VARCHAR)                   AS currency,
    CAST(Rate AS DECIMAL(19,6))                 AS rate,
    CAST(DiscPrcnt AS DECIMAL(19,6))            AS disc_prcnt,
    CAST(LineTotal AS DECIMAL(19,6))            AS line_total,
    CAST(TotalFrgn AS DECIMAL(19,6))            AS total_frgn,
    CAST(TotalSumSy AS DECIMAL(19,6))           AS total_sum_sy,
    CAST(GrssProfit AS DECIMAL(19,6))           AS grss_profit,
    CAST(GrssProfFC AS DECIMAL(19,6))           AS grss_prof_fc,
    CAST(GrssProfSC AS DECIMAL(19,6))           AS grss_prof_sc,
    CAST(StockPrice AS DECIMAL(19,6))           AS stock_price,
    CAST(WhsCode AS VARCHAR)                    AS whs_code,
    CAST(ShipDate AS TIMESTAMP)                 AS ship_date,
    CAST(VatPrcnt AS DECIMAL(19,6))             AS vat_prcnt,
    CAST(VatSum AS DECIMAL(19,6))               AS vat_sum,
    CAST(AcctCode AS VARCHAR)                   AS acct_code,
    CAST(OcrCode AS VARCHAR)                    AS ocr_code,
    CAST(LineType AS VARCHAR)                   AS line_type,
    CAST(TreeType AS VARCHAR)                   AS tree_type,
    CAST(ObjType AS VARCHAR)                    AS obj_type,
    CAST(VisOrder AS BIGINT)                    AS vis_order,
    _company                              AS company,
    _source_updated_at                    AS source_updated_at,
    load_date
FROM current_lines
ORDER BY company, doc_entry, line_num
$seed$, $seed$Deliveries lines, read through ODLN: quantities, prices, line totals in three currencies, base/target links.$seed$, $seed${}$seed$::jsonb, $seed$$seed$, NOW(), :'workspace_id'::uuid),
($seed$sap_b1_goods_receipt_lines$seed$, $seed$silver$seed$, $seed$sap_b1$seed$, $seed$["raw/sap_b1/OPDN", "raw/sap_b1/PDN1", "raw/sap_b1/OADM", "raw/sap_b1/IntercompanyPartners"]$seed$::jsonb, $seed$
-- sap_b1_goods_receipt_lines  (silver)  cartridge: sap_b1
-- sources: ["raw/sap_b1/OPDN", "raw/sap_b1/PDN1", "raw/sap_b1/OADM", "raw/sap_b1/IntercompanyPartners"]
-- description: goods receipt PO lines with their header (OPDN/PDN1, ObjType 20): amounts in document, local and system currency, the cost the line carries, and the intercompany flag from the configured partner mapping. CANCELED is kept (N/Y/C); gold filters it.

WITH headers AS (
    -- Current version of every header per company over the whole bronze history.
    SELECT * EXCLUDE (_rn)
    FROM (
        SELECT *,
               ROW_NUMBER() OVER (
                   PARTITION BY _company, DocEntry
                   ORDER BY _source_updated_at DESC NULLS LAST, load_date DESC, _extracted_at DESC
               ) AS _rn
        FROM read_parquet('s3://{bucket}/raw/sap_b1/OPDN/**/*.parquet',
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
        FROM read_parquet('s3://{bucket}/raw/sap_b1/PDN1/**/*.parquet',
                          hive_partitioning = true,
                          union_by_name   = true)
    )
    WHERE _rn = 1
),
lines AS (
    -- Only the lines that carry the header's latest stamp: a line dropped from
    -- the document disappears the moment the document is re-read.
    SELECT * EXCLUDE (_header_stamp)
    FROM (
        SELECT *,
               MAX(_source_updated_at) OVER (PARTITION BY _company, DocEntry) AS _header_stamp
        FROM line_versions
    )
    WHERE _source_updated_at IS NULL OR _source_updated_at = _header_stamp
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
),
partners AS (
    -- Newest run per company (all of its batches), never a mix of two runs.
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
    -- Currency, explicit on every row: the document's, the company's local
    -- and the company's system currency. DocRate is 0 on a local-currency
    -- document, as Business One stores it.
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
    -- The cost the line carries: Business One's stock price at posting time
    -- times the quantity, and its own gross profit figures.
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
$seed$, $seed$goods receipt PO lines with their header (OPDN/PDN1, ObjType 20): amounts in document, local and system currency, the cost the line carries, and the intercompany flag from the configured partner mapping. CANCELED is kept (N/Y/C); gold filters it.$seed$, $seed${}$seed$::jsonb, $seed$$seed$, NOW(), :'workspace_id'::uuid),
($seed$sap_b1_ibt1_latest$seed$, $seed$silver$seed$, $seed$sap_b1$seed$, $seed$["raw/sap_b1/IBT1"]$seed$::jsonb, $seed$
-- sap_b1_ibt1_latest  (silver)  cartridge: sap_b1
-- sources: ["raw/sap_b1/IBT1"]
-- description: Batch quantities per document line, Direction 0 in / 1 out; rows never change, LogEntry (identity, validate in HANA) is the watermark and page key.

WITH latest AS (
    -- Las filas nunca cambian en la fuente; el mismo registro puede llegar
    -- más de una vez (carga completa + incremental), así que se deduplica
    -- por clave sobre todo el histórico bronze.
    SELECT *
    FROM (
        SELECT *,
               ROW_NUMBER() OVER (
                   PARTITION BY _company, LogEntry
                   ORDER BY load_date DESC, _extracted_at DESC
               ) AS _rn
        FROM read_parquet('s3://{bucket}/raw/sap_b1/IBT1/**/*.parquet',
                      hive_partitioning = true,
                      union_by_name   = true)
    )
    WHERE _rn = 1
)
SELECT
    CAST(LogEntry AS BIGINT)                    AS log_entry,
    CAST(ItemCode AS VARCHAR)                   AS item_code,
    CAST(BatchNum AS VARCHAR)                   AS batch_num,
    CAST(WhsCode AS VARCHAR)                    AS whs_code,
    CAST(BaseType AS BIGINT)                    AS base_type,
    CAST(BaseEntry AS BIGINT)                   AS base_entry,
    CAST(BaseLinNum AS BIGINT)                  AS base_lin_num,
    CAST(Quantity AS DECIMAL(19,6))             AS quantity,
    CAST(Direction AS BIGINT)                   AS direction,
    CAST(DocDate AS TIMESTAMP)                  AS doc_date,
    _company                              AS company,
    _source_updated_at                    AS source_updated_at,
    load_date
FROM latest
ORDER BY company, log_entry
$seed$, $seed$Batch quantities per document line, Direction 0 in / 1 out; rows never change, LogEntry (identity, validate in HANA) is the watermark and page key.$seed$, $seed${}$seed$::jsonb, $seed$$seed$, NOW(), :'workspace_id'::uuid),
($seed$sap_b1_intercompany_reconciliation_month$seed$, $seed$gold$seed$, $seed$sap_b1$seed$, $seed$["raw/sap_b1/OINV", "raw/sap_b1/INV1", "raw/sap_b1/OPCH", "raw/sap_b1/PCH1", "raw/sap_b1/OADM", "raw/sap_b1/IntercompanyPartners"]$seed$::jsonb, $seed$
-- sap_b1_intercompany_reconciliation_month  (gold)  cartridge: sap_b1
-- sources: ["raw/sap_b1/OINV", "raw/sap_b1/INV1", "raw/sap_b1/OPCH", "raw/sap_b1/PCH1", "raw/sap_b1/OADM", "raw/sap_b1/IntercompanyPartners"]
-- description: What consolidation eliminates, proven on both sides: per seller, buyer and month, the seller's invoices to the buyer against the buyer's supplier invoices from the seller, and the difference. A non-zero difference is a capture or timing gap to explain, not something to hide.

WITH sold AS (
    SELECT company AS seller, counterparty_company AS buyer, doc_month, local_currency,
           COUNT(DISTINCT doc_entry) AS seller_invoices,
           SUM(amount_local)         AS sold_local
    FROM read_parquet('s3://{bucket}/silver/sap_b1/sap_b1_ar_invoice_lines/**/*.parquet')
    WHERE canceled = 'N' AND is_intercompany
    GROUP BY 1, 2, 3, 4
),
bought AS (
    SELECT counterparty_company AS seller, company AS buyer, doc_month, local_currency,
           COUNT(DISTINCT doc_entry) AS buyer_invoices,
           SUM(amount_local)         AS bought_local
    FROM read_parquet('s3://{bucket}/silver/sap_b1/sap_b1_ap_invoice_lines/**/*.parquet')
    WHERE canceled = 'N' AND is_intercompany
    GROUP BY 1, 2, 3, 4
)
SELECT
    COALESCE(s.seller, b.seller)                            AS seller,
    COALESCE(s.buyer, b.buyer)                              AS buyer,
    COALESCE(s.doc_month, b.doc_month)                      AS doc_month,
    COALESCE(s.local_currency, b.local_currency)            AS local_currency,
    COALESCE(s.seller_invoices, 0)                          AS seller_invoices,
    COALESCE(b.buyer_invoices, 0)                           AS buyer_invoices,
    ROUND(COALESCE(s.sold_local, 0), 2)                     AS sold_local,
    ROUND(COALESCE(b.bought_local, 0), 2)                   AS bought_local,
    ROUND(COALESCE(s.sold_local, 0) - COALESCE(b.bought_local, 0), 2) AS difference_local,
    COALESCE(s.sold_local, 0) = COALESCE(b.bought_local, 0) AS reconciled
FROM sold s
FULL OUTER JOIN bought b
  ON b.seller = s.seller AND b.buyer = s.buyer AND b.doc_month = s.doc_month AND b.local_currency = s.local_currency
ORDER BY seller, buyer, doc_month
$seed$, $seed$What consolidation eliminates, proven on both sides: per seller, buyer and month, the seller's invoices to the buyer against the buyer's supplier invoices from the seller, and the difference. A non-zero difference is a capture or timing gap to explain, not something to hide.$seed$, $seed${}$seed$::jsonb, $seed$$seed$, NOW(), :'workspace_id'::uuid),
($seed$sap_b1_inv1_latest$seed$, $seed$silver$seed$, $seed$sap_b1$seed$, $seed$["raw/sap_b1/INV1"]$seed$::jsonb, $seed$
-- sap_b1_inv1_latest  (silver)  cartridge: sap_b1
-- sources: ["raw/sap_b1/INV1"]
-- description: A/R invoices lines, read through OINV: quantities, prices, line totals in three currencies, base/target links.

WITH versions AS (
    -- Estado ACTUAL por clave sobre TODO el histórico bronze; después solo
    -- las líneas que llevan la marca más reciente de su cabecera: una línea
    -- borrada del documento desaparece en cuanto la cabecera se relee.
    SELECT *
    FROM (
        SELECT *,
               ROW_NUMBER() OVER (
                   PARTITION BY _company, DocEntry, LineNum
                   ORDER BY _source_updated_at DESC NULLS LAST, load_date DESC, _extracted_at DESC
               ) AS _rn
        FROM read_parquet('s3://{bucket}/raw/sap_b1/INV1/**/*.parquet',
                      hive_partitioning = true,
                      union_by_name   = true)
    )
    WHERE _rn = 1
),
current_lines AS (
    SELECT *
    FROM (
        SELECT *,
               MAX(_source_updated_at) OVER (PARTITION BY _company, DocEntry) AS _header_stamp
        FROM versions
    )
    WHERE _source_updated_at IS NULL OR _source_updated_at = _header_stamp
)
SELECT
    CAST(DocEntry AS BIGINT)                    AS doc_entry,
    CAST(LineNum AS BIGINT)                     AS line_num,
    CAST(TargetType AS BIGINT)                  AS target_type,
    CAST(TrgetEntry AS BIGINT)                  AS trget_entry,
    CAST(BaseType AS BIGINT)                    AS base_type,
    CAST(BaseEntry AS BIGINT)                   AS base_entry,
    CAST(BaseLine AS BIGINT)                    AS base_line,
    CAST(LineStatus AS VARCHAR)                 AS line_status,
    CAST(ItemCode AS VARCHAR)                   AS item_code,
    CAST(Dscription AS VARCHAR)                 AS dscription,
    CAST(Quantity AS DECIMAL(19,6))             AS quantity,
    CAST(OpenQty AS DECIMAL(19,6))              AS open_qty,
    CAST(Price AS DECIMAL(19,6))                AS price,
    CAST(PriceBefDi AS DECIMAL(19,6))           AS price_bef_di,
    CAST(Currency AS VARCHAR)                   AS currency,
    CAST(Rate AS DECIMAL(19,6))                 AS rate,
    CAST(DiscPrcnt AS DECIMAL(19,6))            AS disc_prcnt,
    CAST(LineTotal AS DECIMAL(19,6))            AS line_total,
    CAST(TotalFrgn AS DECIMAL(19,6))            AS total_frgn,
    CAST(TotalSumSy AS DECIMAL(19,6))           AS total_sum_sy,
    CAST(GrssProfit AS DECIMAL(19,6))           AS grss_profit,
    CAST(GrssProfFC AS DECIMAL(19,6))           AS grss_prof_fc,
    CAST(GrssProfSC AS DECIMAL(19,6))           AS grss_prof_sc,
    CAST(StockPrice AS DECIMAL(19,6))           AS stock_price,
    CAST(WhsCode AS VARCHAR)                    AS whs_code,
    CAST(ShipDate AS TIMESTAMP)                 AS ship_date,
    CAST(VatPrcnt AS DECIMAL(19,6))             AS vat_prcnt,
    CAST(VatSum AS DECIMAL(19,6))               AS vat_sum,
    CAST(AcctCode AS VARCHAR)                   AS acct_code,
    CAST(OcrCode AS VARCHAR)                    AS ocr_code,
    CAST(LineType AS VARCHAR)                   AS line_type,
    CAST(TreeType AS VARCHAR)                   AS tree_type,
    CAST(ObjType AS VARCHAR)                    AS obj_type,
    CAST(VisOrder AS BIGINT)                    AS vis_order,
    _company                              AS company,
    _source_updated_at                    AS source_updated_at,
    load_date
FROM current_lines
ORDER BY company, doc_entry, line_num
$seed$, $seed$A/R invoices lines, read through OINV: quantities, prices, line totals in three currencies, base/target links.$seed$, $seed${}$seed$::jsonb, $seed$$seed$, NOW(), :'workspace_id'::uuid),
($seed$sap_b1_inventory_movements$seed$, $seed$silver$seed$, $seed$sap_b1$seed$, $seed$["raw/sap_b1/OINM", "raw/sap_b1/OITM", "raw/sap_b1/OADM"]$seed$::jsonb, $seed$
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
$seed$, $seed$Every stock movement per company (OINM, immutable), with the item's name and group and the company's currencies; net quantity and value per row.$seed$, $seed${}$seed$::jsonb, $seed$$seed$, NOW(), :'workspace_id'::uuid),
($seed$sap_b1_items$seed$, $seed$silver$seed$, $seed$sap_b1$seed$, $seed$["raw/sap_b1/OITM", "raw/sap_b1/OITB"]$seed$::jsonb, $seed$
-- sap_b1_items  (silver)  cartridge: sap_b1
-- sources: ["raw/sap_b1/OITM", "raw/sap_b1/OITB"]
-- description: Current item master per company with its item group, the inventory/sales/purchase flags, batch management and the default warehouse.

WITH oitm AS (
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
groups AS (
    -- Newest run per company (all of its batches), never a mix of two runs.
    SELECT * EXCLUDE (_rn)
    FROM (
        SELECT s.*, ROW_NUMBER() OVER (PARTITION BY s._company, s.ItmsGrpCod ORDER BY s._extracted_at DESC) AS _rn
        FROM read_parquet('s3://{bucket}/raw/sap_b1/OITB/**/*.parquet',
                          hive_partitioning = true,
                          union_by_name   = true) s
        JOIN (
            SELECT _company, arg_max(regexp_replace(_run_id, '-b[0-9]+$', ''), _extracted_at) AS _run_key
            FROM read_parquet('s3://{bucket}/raw/sap_b1/OITB/**/*.parquet',
                          hive_partitioning = true,
                          union_by_name   = true)
            GROUP BY _company
        ) n ON n._company = s._company AND regexp_replace(s._run_id, '-b[0-9]+$', '') = n._run_key
    )
    WHERE _rn = 1
)
SELECT
    i._company                              AS company,
    CAST(i.ItemCode AS VARCHAR)             AS item_code,
    CAST(i.ItemName AS VARCHAR)             AS item_name,
    CAST(i.ItmsGrpCod AS BIGINT)            AS item_group_code,
    CAST(g.ItmsGrpNam AS VARCHAR)           AS item_group_name,
    CAST(i.InvntItem AS VARCHAR) = 'Y'      AS inventory_item,
    CAST(i.SellItem AS VARCHAR) = 'Y'       AS sell_item,
    CAST(i.PrchseItem AS VARCHAR) = 'Y'     AS purchase_item,
    CAST(i.ManBtchNum AS VARCHAR) = 'Y'     AS batch_managed,
    CAST(i.DfltWH AS VARCHAR)               AS default_warehouse,
    CAST(i.AvgPrice AS DECIMAL(19,6))       AS avg_price,
    CAST(i.LastPurPrc AS DECIMAL(19,6))     AS last_purchase_price,
    CAST(i.validFor AS VARCHAR)             AS valid_for,
    CAST(i.frozenFor AS VARCHAR)            AS frozen_for,
    CAST(i.CreateDate AS TIMESTAMP)         AS created_at,
    i._source_updated_at                    AS source_updated_at,
    i.load_date
FROM oitm i
LEFT JOIN groups g ON g._company = i._company AND g.ItmsGrpCod = i.ItmsGrpCod
ORDER BY company, item_code
$seed$, $seed$Current item master per company with its item group, the inventory/sales/purchase flags, batch management and the default warehouse.$seed$, $seed${}$seed$::jsonb, $seed$$seed$, NOW(), :'workspace_id'::uuid),
($seed$sap_b1_itt1_latest$seed$, $seed$silver$seed$, $seed$sap_b1$seed$, $seed$["raw/sap_b1/ITT1"]$seed$::jsonb, $seed$
-- sap_b1_itt1_latest  (silver)  cartridge: sap_b1
-- sources: ["raw/sap_b1/ITT1"]
-- description: Components per BOM; read through OITT so an edited BOM brings all its lines.

WITH versions AS (
    -- Estado ACTUAL por clave sobre TODO el histórico bronze; después solo
    -- las líneas que llevan la marca más reciente de su cabecera: una línea
    -- borrada del documento desaparece en cuanto la cabecera se relee.
    SELECT *
    FROM (
        SELECT *,
               ROW_NUMBER() OVER (
                   PARTITION BY _company, Father, ChildNum
                   ORDER BY _source_updated_at DESC NULLS LAST, load_date DESC, _extracted_at DESC
               ) AS _rn
        FROM read_parquet('s3://{bucket}/raw/sap_b1/ITT1/**/*.parquet',
                      hive_partitioning = true,
                      union_by_name   = true)
    )
    WHERE _rn = 1
),
current_lines AS (
    SELECT *
    FROM (
        SELECT *,
               MAX(_source_updated_at) OVER (PARTITION BY _company, Father) AS _header_stamp
        FROM versions
    )
    WHERE _source_updated_at IS NULL OR _source_updated_at = _header_stamp
)
SELECT
    CAST(Father AS VARCHAR)                     AS father,
    CAST(ChildNum AS BIGINT)                    AS child_num,
    CAST(Code AS VARCHAR)                       AS code,
    CAST(Quantity AS DECIMAL(19,6))             AS quantity,
    CAST(Warehouse AS VARCHAR)                  AS warehouse,
    CAST(IssueMthd AS VARCHAR)                  AS issue_mthd,
    CAST(PriceList AS BIGINT)                   AS price_list,
    _company                              AS company,
    _source_updated_at                    AS source_updated_at,
    load_date
FROM current_lines
ORDER BY company, father, child_num
$seed$, $seed$Components per BOM; read through OITT so an edited BOM brings all its lines.$seed$, $seed${}$seed$::jsonb, $seed$$seed$, NOW(), :'workspace_id'::uuid),
($seed$sap_b1_jdt1_latest$seed$, $seed$silver$seed$, $seed$sap_b1$seed$, $seed$["raw/sap_b1/JDT1"]$seed$::jsonb, $seed$
-- sap_b1_jdt1_latest  (silver)  cartridge: sap_b1
-- sources: ["raw/sap_b1/JDT1"]
-- description: Journal lines in local, foreign and system currency; ShortName carries the CardCode on control-account lines.

WITH versions AS (
    -- Estado ACTUAL por clave sobre TODO el histórico bronze; después solo
    -- las líneas que llevan la marca más reciente de su cabecera: una línea
    -- borrada del documento desaparece en cuanto la cabecera se relee.
    SELECT *
    FROM (
        SELECT *,
               ROW_NUMBER() OVER (
                   PARTITION BY _company, TransId, Line_ID
                   ORDER BY _source_updated_at DESC NULLS LAST, load_date DESC, _extracted_at DESC
               ) AS _rn
        FROM read_parquet('s3://{bucket}/raw/sap_b1/JDT1/**/*.parquet',
                      hive_partitioning = true,
                      union_by_name   = true)
    )
    WHERE _rn = 1
),
current_lines AS (
    SELECT *
    FROM (
        SELECT *,
               MAX(_source_updated_at) OVER (PARTITION BY _company, TransId) AS _header_stamp
        FROM versions
    )
    WHERE _source_updated_at IS NULL OR _source_updated_at = _header_stamp
)
SELECT
    CAST(TransId AS BIGINT)                     AS trans_id,
    CAST(Line_ID AS BIGINT)                     AS line_id,
    CAST(Account AS VARCHAR)                    AS account,
    CAST(ShortName AS VARCHAR)                  AS short_name,
    CAST(ContraAct AS VARCHAR)                  AS contra_act,
    CAST(Debit AS DECIMAL(19,6))                AS debit,
    CAST(Credit AS DECIMAL(19,6))               AS credit,
    CAST(FCDebit AS DECIMAL(19,6))              AS fc_debit,
    CAST(FCCredit AS DECIMAL(19,6))             AS fc_credit,
    CAST(FCCurrency AS VARCHAR)                 AS fc_currency,
    CAST(SYSDeb AS DECIMAL(19,6))               AS sys_deb,
    CAST(SYSCred AS DECIMAL(19,6))              AS sys_cred,
    CAST(ProfitCode AS VARCHAR)                 AS profit_code,
    CAST(RefDate AS TIMESTAMP)                  AS ref_date,
    CAST(DueDate AS TIMESTAMP)                  AS due_date,
    CAST(TaxDate AS TIMESTAMP)                  AS tax_date,
    CAST(BaseRef AS VARCHAR)                    AS base_ref,
    CAST(TransType AS VARCHAR)                  AS trans_type,
    CAST(ObjType AS VARCHAR)                    AS obj_type,
    CAST(LineMemo AS VARCHAR)                   AS line_memo,
    _company                              AS company,
    _source_updated_at                    AS source_updated_at,
    load_date
FROM current_lines
ORDER BY company, trans_id, line_id
$seed$, $seed$Journal lines in local, foreign and system currency; ShortName carries the CardCode on control-account lines.$seed$, $seed${}$seed$::jsonb, $seed$$seed$, NOW(), :'workspace_id'::uuid),
($seed$sap_b1_journal_lines$seed$, $seed$silver$seed$, $seed$sap_b1$seed$, $seed$["raw/sap_b1/OJDT", "raw/sap_b1/JDT1", "raw/sap_b1/OACT", "raw/sap_b1/OADM", "raw/sap_b1/IntercompanyPartners"]$seed$::jsonb, $seed$
-- sap_b1_journal_lines  (silver)  cartridge: sap_b1
-- sources: ["raw/sap_b1/OJDT", "raw/sap_b1/JDT1", "raw/sap_b1/OACT", "raw/sap_b1/OADM", "raw/sap_b1/IntercompanyPartners"]
-- description: Journal entry lines with their entry header, the account's type (N balance sheet, I income, E expense), the partner code that control-account lines carry in ShortName, and amounts in local, foreign and system currency.

WITH entries AS (
    SELECT * EXCLUDE (_rn)
    FROM (
        SELECT *,
               ROW_NUMBER() OVER (
                   PARTITION BY _company, TransId
                   ORDER BY _source_updated_at DESC NULLS LAST, load_date DESC, _extracted_at DESC
               ) AS _rn
        FROM read_parquet('s3://{bucket}/raw/sap_b1/OJDT/**/*.parquet',
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
                   PARTITION BY _company, TransId, Line_ID
                   ORDER BY _source_updated_at DESC NULLS LAST, load_date DESC, _extracted_at DESC
               ) AS _rn
        FROM read_parquet('s3://{bucket}/raw/sap_b1/JDT1/**/*.parquet',
                          hive_partitioning = true,
                          union_by_name   = true)
    )
    WHERE _rn = 1
),
lines AS (
    SELECT * EXCLUDE (_entry_stamp)
    FROM (
        SELECT *, MAX(_source_updated_at) OVER (PARTITION BY _company, TransId) AS _entry_stamp
        FROM line_versions
    )
    WHERE _source_updated_at IS NULL OR _source_updated_at = _entry_stamp
),
accounts AS (
    -- Newest run per company (all of its batches), never a mix of two runs.
    SELECT * EXCLUDE (_rn)
    FROM (
        SELECT s.*, ROW_NUMBER() OVER (PARTITION BY s._company, s.AcctCode ORDER BY s._extracted_at DESC) AS _rn
        FROM read_parquet('s3://{bucket}/raw/sap_b1/OACT/**/*.parquet',
                          hive_partitioning = true,
                          union_by_name   = true) s
        JOIN (
            SELECT _company, arg_max(regexp_replace(_run_id, '-b[0-9]+$', ''), _extracted_at) AS _run_key
            FROM read_parquet('s3://{bucket}/raw/sap_b1/OACT/**/*.parquet',
                          hive_partitioning = true,
                          union_by_name   = true)
            GROUP BY _company
        ) n ON n._company = s._company AND regexp_replace(s._run_id, '-b[0-9]+$', '') = n._run_key
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
),
partners AS (
    -- Newest run per company (all of its batches), never a mix of two runs.
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
    e._company                                              AS company,
    CAST(e.TransId AS BIGINT)                               AS trans_id,
    CAST(l.Line_ID AS BIGINT)                               AS line_id,
    CAST(e.Number AS BIGINT)                                AS entry_number,
    CAST(e.RefDate AS TIMESTAMP)                            AS ref_date,
    CAST(DATE_TRUNC('month', CAST(e.RefDate AS TIMESTAMP)) AS DATE) AS doc_month,
    CAST(e.DueDate AS TIMESTAMP)                            AS due_date,
    CAST(e.TaxDate AS TIMESTAMP)                            AS tax_date,
    CAST(e.TransType AS VARCHAR)                            AS origin_obj_type,
    CAST(e.BaseRef AS VARCHAR)                              AS base_ref,
    CAST(e.StornoToTr AS BIGINT)                            AS reverses_trans_id,
    CAST(e.Memo AS VARCHAR)                                 AS memo,
    CAST(l.Account AS VARCHAR)                              AS account,
    CAST(a.AcctName AS VARCHAR)                             AS account_name,
    CAST(a.ActType AS VARCHAR)                              AS account_type,
    CAST(a.Postable AS VARCHAR) = 'Y'                       AS postable,
    CAST(l.ShortName AS VARCHAR)                            AS short_name,
    CASE WHEN CAST(l.ShortName AS VARCHAR) <> CAST(l.Account AS VARCHAR)
         THEN CAST(l.ShortName AS VARCHAR) END              AS partner_code,
    p.CardCode IS NOT NULL                                  AS is_intercompany_partner,
    CAST(p.CounterpartyCompany AS VARCHAR)                  AS counterparty_company,
    CAST(l.ContraAct AS VARCHAR)                            AS contra_account,
    CAST(l.ProfitCode AS VARCHAR)                           AS cost_centre,
    c.local_currency                                        AS local_currency,
    c.sys_currency                                          AS sys_currency,
    CAST(l.Debit AS DECIMAL(19,6))                          AS debit_local,
    CAST(l.Credit AS DECIMAL(19,6))                         AS credit_local,
    CAST(l.Debit - l.Credit AS DECIMAL(19,6))               AS net_debit_local,
    CAST(l.FCCurrency AS VARCHAR)                           AS fc_currency,
    CAST(l.FCDebit AS DECIMAL(19,6))                        AS debit_fc,
    CAST(l.FCCredit AS DECIMAL(19,6))                       AS credit_fc,
    CAST(l.SYSDeb AS DECIMAL(19,6))                         AS debit_sys,
    CAST(l.SYSCred AS DECIMAL(19,6))                        AS credit_sys,
    CAST(l.SYSDeb - l.SYSCred AS DECIMAL(19,6))             AS net_debit_sys,
    CAST(l.ObjType AS VARCHAR)                              AS line_obj_type,
    CAST(l.LineMemo AS VARCHAR)                             AS line_memo,
    e._source_updated_at                                    AS source_updated_at,
    e.load_date
FROM lines l
JOIN entries e ON e._company = l._company AND e.TransId = l.TransId
LEFT JOIN accounts a ON a._company = l._company AND a.AcctCode = l.Account
LEFT JOIN company c ON c._company = e._company
LEFT JOIN partners p ON p._company = l._company AND p.CardCode = l.ShortName
ORDER BY company, trans_id, line_id
$seed$, $seed$Journal entry lines with their entry header, the account's type (N balance sheet, I income, E expense), the partner code that control-account lines carry in ShortName, and amounts in local, foreign and system currency.$seed$, $seed${}$seed$::jsonb, $seed$$seed$, NOW(), :'workspace_id'::uuid),
($seed$sap_b1_oact_latest$seed$, $seed$silver$seed$, $seed$sap_b1$seed$, $seed$["raw/sap_b1/OACT"]$seed$::jsonb, $seed$
-- sap_b1_oact_latest  (silver)  cartridge: sap_b1
-- sources: ["raw/sap_b1/OACT"]
-- description: G/L accounts: ActType N (P&L), I (income), E (expense); Postable flag.

WITH newest_run AS (
    -- Instantánea sin marca de agua: la corrida más reciente POR EMPRESA
    -- (todos sus lotes) y nada más. Deduplicar el histórico resucitaría
    -- filas que la fuente borró; quedarse con MAX(load_date) mezclaría dos
    -- corridas del mismo día.
    SELECT _company,
           arg_max(regexp_replace(_run_id, '-b[0-9]+$', ''), _extracted_at) AS _run_key
    FROM read_parquet('s3://{bucket}/raw/sap_b1/OACT/**/*.parquet',
                      hive_partitioning = true,
                      union_by_name   = true)
    GROUP BY _company
),
latest AS (
    SELECT *
    FROM (
        SELECT s.*,
               ROW_NUMBER() OVER (PARTITION BY s._company, s.AcctCode ORDER BY s._extracted_at DESC) AS _rn
        FROM read_parquet('s3://{bucket}/raw/sap_b1/OACT/**/*.parquet',
                      hive_partitioning = true,
                      union_by_name   = true) s
        JOIN newest_run n
          ON n._company = s._company
         AND regexp_replace(s._run_id, '-b[0-9]+$', '') = n._run_key
    )
    WHERE _rn = 1
)
SELECT
    CAST(AcctCode AS VARCHAR)                   AS acct_code,
    CAST(AcctName AS VARCHAR)                   AS acct_name,
    CAST(Postable AS VARCHAR)                   AS postable,
    CAST(ActType AS VARCHAR)                    AS act_type,
    CAST(FatherNum AS VARCHAR)                  AS father_num,
    CAST(Levels AS BIGINT)                      AS levels,
    CAST(GroupMask AS BIGINT)                   AS group_mask,
    _company                              AS company,
    load_date
FROM latest
ORDER BY company, acct_code
$seed$, $seed$G/L accounts: ActType N (P&L), I (income), E (expense); Postable flag.$seed$, $seed${}$seed$::jsonb, $seed$$seed$, NOW(), :'workspace_id'::uuid),
($seed$sap_b1_oadm_latest$seed$, $seed$silver$seed$, $seed$sap_b1$seed$, $seed$["raw/sap_b1/OADM"]$seed$::jsonb, $seed$
-- sap_b1_oadm_latest  (silver)  cartridge: sap_b1
-- sources: ["raw/sap_b1/OADM"]
-- description: Company code, local currency (MainCurncy) and system currency (SysCurrncy).

WITH newest_run AS (
    -- Instantánea sin marca de agua: la corrida más reciente POR EMPRESA
    -- (todos sus lotes) y nada más. Deduplicar el histórico resucitaría
    -- filas que la fuente borró; quedarse con MAX(load_date) mezclaría dos
    -- corridas del mismo día.
    SELECT _company,
           arg_max(regexp_replace(_run_id, '-b[0-9]+$', ''), _extracted_at) AS _run_key
    FROM read_parquet('s3://{bucket}/raw/sap_b1/OADM/**/*.parquet',
                      hive_partitioning = true,
                      union_by_name   = true)
    GROUP BY _company
),
latest AS (
    SELECT *
    FROM (
        SELECT s.*,
               ROW_NUMBER() OVER (PARTITION BY s._company, s.Code ORDER BY s._extracted_at DESC) AS _rn
        FROM read_parquet('s3://{bucket}/raw/sap_b1/OADM/**/*.parquet',
                      hive_partitioning = true,
                      union_by_name   = true) s
        JOIN newest_run n
          ON n._company = s._company
         AND regexp_replace(s._run_id, '-b[0-9]+$', '') = n._run_key
    )
    WHERE _rn = 1
)
SELECT
    CAST(Code AS VARCHAR)                       AS code,
    CAST(CompnyName AS VARCHAR)                 AS compny_name,
    CAST(MainCurncy AS VARCHAR)                 AS main_curncy,
    CAST(SysCurrncy AS VARCHAR)                 AS sys_currncy,
    CAST(Country AS VARCHAR)                    AS country,
    _company                              AS company,
    load_date
FROM latest
ORDER BY company, code
$seed$, $seed$Company code, local currency (MainCurncy) and system currency (SysCurrncy).$seed$, $seed${}$seed$::jsonb, $seed$$seed$, NOW(), :'workspace_id'::uuid),
($seed$sap_b1_obtn_latest$seed$, $seed$silver$seed$, $seed$sap_b1$seed$, $seed$["raw/sap_b1/OBTN"]$seed$::jsonb, $seed$
-- sap_b1_obtn_latest  (silver)  cartridge: sap_b1
-- sources: ["raw/sap_b1/OBTN"]
-- description: Batch master (DistNumber, manufacture and expiry dates).

WITH newest_run AS (
    -- Instantánea sin marca de agua: la corrida más reciente POR EMPRESA
    -- (todos sus lotes) y nada más. Deduplicar el histórico resucitaría
    -- filas que la fuente borró; quedarse con MAX(load_date) mezclaría dos
    -- corridas del mismo día.
    SELECT _company,
           arg_max(regexp_replace(_run_id, '-b[0-9]+$', ''), _extracted_at) AS _run_key
    FROM read_parquet('s3://{bucket}/raw/sap_b1/OBTN/**/*.parquet',
                      hive_partitioning = true,
                      union_by_name   = true)
    GROUP BY _company
),
latest AS (
    SELECT *
    FROM (
        SELECT s.*,
               ROW_NUMBER() OVER (PARTITION BY s._company, s.AbsEntry ORDER BY s._extracted_at DESC) AS _rn
        FROM read_parquet('s3://{bucket}/raw/sap_b1/OBTN/**/*.parquet',
                      hive_partitioning = true,
                      union_by_name   = true) s
        JOIN newest_run n
          ON n._company = s._company
         AND regexp_replace(s._run_id, '-b[0-9]+$', '') = n._run_key
    )
    WHERE _rn = 1
)
SELECT
    CAST(AbsEntry AS BIGINT)                    AS abs_entry,
    CAST(ItemCode AS VARCHAR)                   AS item_code,
    CAST(SysNumber AS BIGINT)                   AS sys_number,
    CAST(DistNumber AS VARCHAR)                 AS dist_number,
    CAST(MnfDate AS TIMESTAMP)                  AS mnf_date,
    CAST(ExpDate AS TIMESTAMP)                  AS exp_date,
    CAST(InDate AS TIMESTAMP)                   AS in_date,
    CAST(Status AS BIGINT)                      AS status,
    _company                              AS company,
    load_date
FROM latest
ORDER BY company, abs_entry
$seed$, $seed$Batch master (DistNumber, manufacture and expiry dates).$seed$, $seed${}$seed$::jsonb, $seed$$seed$, NOW(), :'workspace_id'::uuid),
($seed$sap_b1_obtq_latest$seed$, $seed$silver$seed$, $seed$sap_b1$seed$, $seed$["raw/sap_b1/OBTQ"]$seed$::jsonb, $seed$
-- sap_b1_obtq_latest  (silver)  cartridge: sap_b1
-- sources: ["raw/sap_b1/OBTQ"]
-- description: Batch quantity per item, batch (SysNumber) and warehouse (snapshot).

WITH newest_run AS (
    -- Instantánea sin marca de agua: la corrida más reciente POR EMPRESA
    -- (todos sus lotes) y nada más. Deduplicar el histórico resucitaría
    -- filas que la fuente borró; quedarse con MAX(load_date) mezclaría dos
    -- corridas del mismo día.
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
$seed$, $seed$Batch quantity per item, batch (SysNumber) and warehouse (snapshot).$seed$, $seed${}$seed$::jsonb, $seed$$seed$, NOW(), :'workspace_id'::uuid),
($seed$sap_b1_ocrd_latest$seed$, $seed$silver$seed$, $seed$sap_b1$seed$, $seed$["raw/sap_b1/OCRD"]$seed$::jsonb, $seed$
-- sap_b1_ocrd_latest  (silver)  cartridge: sap_b1
-- sources: ["raw/sap_b1/OCRD"]
-- description: Customers (CardType C) and suppliers (CardType S) with their currency and group.

WITH latest AS (
    -- Estado ACTUAL por clave sobre TODO el histórico bronze. La entidad es
    -- incremental: quedarse con MAX(load_date) colapsaría la población al
    -- delta del día. Un borrado en la fuente no se refleja hasta una carga
    -- completa; ver README del cartucho.
    SELECT *
    FROM (
        SELECT *,
               ROW_NUMBER() OVER (
                   PARTITION BY _company, CardCode
                   ORDER BY _source_updated_at DESC NULLS LAST, load_date DESC, _extracted_at DESC
               ) AS _rn
        FROM read_parquet('s3://{bucket}/raw/sap_b1/OCRD/**/*.parquet',
                      hive_partitioning = true,
                      union_by_name   = true)
    )
    WHERE _rn = 1
)
SELECT
    CAST(CardCode AS VARCHAR)                   AS card_code,
    CAST(CardName AS VARCHAR)                   AS card_name,
    CAST(CardType AS VARCHAR)                   AS card_type,
    CAST(GroupCode AS BIGINT)                   AS group_code,
    CAST(Currency AS VARCHAR)                   AS currency,
    CAST(SlpCode AS BIGINT)                     AS slp_code,
    CAST(Country AS VARCHAR)                    AS country,
    CAST(validFor AS VARCHAR)                   AS valid_for,
    CAST(frozenFor AS VARCHAR)                  AS frozen_for,
    CAST(CreateDate AS TIMESTAMP)               AS create_date,
    CAST(UpdateDate AS TIMESTAMP)               AS update_date,
    CAST(UpdateTS AS BIGINT)                    AS update_ts,
    _company                              AS company,
    _source_updated_at                    AS source_updated_at,
    load_date
FROM latest
ORDER BY company, card_code
$seed$, $seed$Customers (CardType C) and suppliers (CardType S) with their currency and group.$seed$, $seed${}$seed$::jsonb, $seed$$seed$, NOW(), :'workspace_id'::uuid),
($seed$sap_b1_ocrg_latest$seed$, $seed$silver$seed$, $seed$sap_b1$seed$, $seed$["raw/sap_b1/OCRG"]$seed$::jsonb, $seed$
-- sap_b1_ocrg_latest  (silver)  cartridge: sap_b1
-- sources: ["raw/sap_b1/OCRG"]
-- description: Customer and supplier groups.

WITH newest_run AS (
    -- Instantánea sin marca de agua: la corrida más reciente POR EMPRESA
    -- (todos sus lotes) y nada más. Deduplicar el histórico resucitaría
    -- filas que la fuente borró; quedarse con MAX(load_date) mezclaría dos
    -- corridas del mismo día.
    SELECT _company,
           arg_max(regexp_replace(_run_id, '-b[0-9]+$', ''), _extracted_at) AS _run_key
    FROM read_parquet('s3://{bucket}/raw/sap_b1/OCRG/**/*.parquet',
                      hive_partitioning = true,
                      union_by_name   = true)
    GROUP BY _company
),
latest AS (
    SELECT *
    FROM (
        SELECT s.*,
               ROW_NUMBER() OVER (PARTITION BY s._company, s.GroupCode ORDER BY s._extracted_at DESC) AS _rn
        FROM read_parquet('s3://{bucket}/raw/sap_b1/OCRG/**/*.parquet',
                      hive_partitioning = true,
                      union_by_name   = true) s
        JOIN newest_run n
          ON n._company = s._company
         AND regexp_replace(s._run_id, '-b[0-9]+$', '') = n._run_key
    )
    WHERE _rn = 1
)
SELECT
    CAST(GroupCode AS BIGINT)                   AS group_code,
    CAST(GroupName AS VARCHAR)                  AS group_name,
    CAST(GroupType AS VARCHAR)                  AS group_type,
    _company                              AS company,
    load_date
FROM latest
ORDER BY company, group_code
$seed$, $seed$Customer and supplier groups.$seed$, $seed${}$seed$::jsonb, $seed$$seed$, NOW(), :'workspace_id'::uuid),
($seed$sap_b1_ocrn_latest$seed$, $seed$silver$seed$, $seed$sap_b1$seed$, $seed$["raw/sap_b1/OCRN"]$seed$::jsonb, $seed$
-- sap_b1_ocrn_latest  (silver)  cartridge: sap_b1
-- sources: ["raw/sap_b1/OCRN"]
-- description: Currency codes configured in the company.

WITH newest_run AS (
    -- Instantánea sin marca de agua: la corrida más reciente POR EMPRESA
    -- (todos sus lotes) y nada más. Deduplicar el histórico resucitaría
    -- filas que la fuente borró; quedarse con MAX(load_date) mezclaría dos
    -- corridas del mismo día.
    SELECT _company,
           arg_max(regexp_replace(_run_id, '-b[0-9]+$', ''), _extracted_at) AS _run_key
    FROM read_parquet('s3://{bucket}/raw/sap_b1/OCRN/**/*.parquet',
                      hive_partitioning = true,
                      union_by_name   = true)
    GROUP BY _company
),
latest AS (
    SELECT *
    FROM (
        SELECT s.*,
               ROW_NUMBER() OVER (PARTITION BY s._company, s.CurrCode ORDER BY s._extracted_at DESC) AS _rn
        FROM read_parquet('s3://{bucket}/raw/sap_b1/OCRN/**/*.parquet',
                      hive_partitioning = true,
                      union_by_name   = true) s
        JOIN newest_run n
          ON n._company = s._company
         AND regexp_replace(s._run_id, '-b[0-9]+$', '') = n._run_key
    )
    WHERE _rn = 1
)
SELECT
    CAST(CurrCode AS VARCHAR)                   AS curr_code,
    CAST(CurrName AS VARCHAR)                   AS curr_name,
    CAST(DocCurrCod AS VARCHAR)                 AS doc_curr_cod,
    _company                              AS company,
    load_date
FROM latest
ORDER BY company, curr_code
$seed$, $seed$Currency codes configured in the company.$seed$, $seed${}$seed$::jsonb, $seed$$seed$, NOW(), :'workspace_id'::uuid),
($seed$sap_b1_odln_latest$seed$, $seed$silver$seed$, $seed$sap_b1$seed$, $seed$["raw/sap_b1/ODLN"]$seed$::jsonb, $seed$
-- sap_b1_odln_latest  (silver)  cartridge: sap_b1
-- sources: ["raw/sap_b1/ODLN"]
-- description: Deliveries headers, ObjType 15: totals in document, local and system currency; CANCELED N/Y/C.

WITH latest AS (
    -- Estado ACTUAL por clave sobre TODO el histórico bronze. La entidad es
    -- incremental: quedarse con MAX(load_date) colapsaría la población al
    -- delta del día. Un borrado en la fuente no se refleja hasta una carga
    -- completa; ver README del cartucho.
    SELECT *
    FROM (
        SELECT *,
               ROW_NUMBER() OVER (
                   PARTITION BY _company, DocEntry
                   ORDER BY _source_updated_at DESC NULLS LAST, load_date DESC, _extracted_at DESC
               ) AS _rn
        FROM read_parquet('s3://{bucket}/raw/sap_b1/ODLN/**/*.parquet',
                      hive_partitioning = true,
                      union_by_name   = true)
    )
    WHERE _rn = 1
)
SELECT
    CAST(DocEntry AS BIGINT)                    AS doc_entry,
    CAST(DocNum AS BIGINT)                      AS doc_num,
    CAST(DocType AS VARCHAR)                    AS doc_type,
    CAST(CANCELED AS VARCHAR)                   AS canceled,
    CAST(DocStatus AS VARCHAR)                  AS doc_status,
    CAST(ObjType AS VARCHAR)                    AS obj_type,
    CAST(DocDate AS TIMESTAMP)                  AS doc_date,
    CAST(DocDueDate AS TIMESTAMP)               AS doc_due_date,
    CAST(TaxDate AS TIMESTAMP)                  AS tax_date,
    CAST(CardCode AS VARCHAR)                   AS card_code,
    CAST(CardName AS VARCHAR)                   AS card_name,
    CAST(NumAtCard AS VARCHAR)                  AS num_at_card,
    CAST(DocCur AS VARCHAR)                     AS doc_cur,
    CAST(DocRate AS DECIMAL(19,6))              AS doc_rate,
    CAST(DocTotal AS DECIMAL(19,6))             AS doc_total,
    CAST(DocTotalFC AS DECIMAL(19,6))           AS doc_total_fc,
    CAST(DocTotalSy AS DECIMAL(19,6))           AS doc_total_sy,
    CAST(VatSum AS DECIMAL(19,6))               AS vat_sum,
    CAST(VatSumFC AS DECIMAL(19,6))             AS vat_sum_fc,
    CAST(VatSumSy AS DECIMAL(19,6))             AS vat_sum_sy,
    CAST(DiscSum AS DECIMAL(19,6))              AS disc_sum,
    CAST(GrosProfit AS DECIMAL(19,6))           AS gros_profit,
    CAST(GrosProfFC AS DECIMAL(19,6))           AS gros_prof_fc,
    CAST(GrosProfSy AS DECIMAL(19,6))           AS gros_prof_sy,
    CAST(SlpCode AS BIGINT)                     AS slp_code,
    CAST(GroupNum AS BIGINT)                    AS group_num,
    CAST(Comments AS VARCHAR)                   AS comments,
    CAST(TransId AS BIGINT)                     AS trans_id,
    CAST(BPLId AS BIGINT)                       AS bpl_id,
    CAST(Series AS BIGINT)                      AS series,
    CAST(CreateDate AS TIMESTAMP)               AS create_date,
    CAST(CreateTS AS BIGINT)                    AS create_ts,
    CAST(UpdateDate AS TIMESTAMP)               AS update_date,
    CAST(UpdateTS AS BIGINT)                    AS update_ts,
    CAST(UserSign AS BIGINT)                    AS user_sign,
    _company                              AS company,
    _source_updated_at                    AS source_updated_at,
    load_date
FROM latest
ORDER BY company, doc_entry
$seed$, $seed$Deliveries headers, ObjType 15: totals in document, local and system currency; CANCELED N/Y/C.$seed$, $seed${}$seed$::jsonb, $seed$$seed$, NOW(), :'workspace_id'::uuid),
($seed$sap_b1_ofpr_latest$seed$, $seed$silver$seed$, $seed$sap_b1$seed$, $seed$["raw/sap_b1/OFPR"]$seed$::jsonb, $seed$
-- sap_b1_ofpr_latest  (silver)  cartridge: sap_b1
-- sources: ["raw/sap_b1/OFPR"]
-- description: Financial posting periods with their date ranges.

WITH newest_run AS (
    -- Instantánea sin marca de agua: la corrida más reciente POR EMPRESA
    -- (todos sus lotes) y nada más. Deduplicar el histórico resucitaría
    -- filas que la fuente borró; quedarse con MAX(load_date) mezclaría dos
    -- corridas del mismo día.
    SELECT _company,
           arg_max(regexp_replace(_run_id, '-b[0-9]+$', ''), _extracted_at) AS _run_key
    FROM read_parquet('s3://{bucket}/raw/sap_b1/OFPR/**/*.parquet',
                      hive_partitioning = true,
                      union_by_name   = true)
    GROUP BY _company
),
latest AS (
    SELECT *
    FROM (
        SELECT s.*,
               ROW_NUMBER() OVER (PARTITION BY s._company, s.AbsEntry ORDER BY s._extracted_at DESC) AS _rn
        FROM read_parquet('s3://{bucket}/raw/sap_b1/OFPR/**/*.parquet',
                      hive_partitioning = true,
                      union_by_name   = true) s
        JOIN newest_run n
          ON n._company = s._company
         AND regexp_replace(s._run_id, '-b[0-9]+$', '') = n._run_key
    )
    WHERE _rn = 1
)
SELECT
    CAST(AbsEntry AS BIGINT)                    AS abs_entry,
    CAST(Code AS VARCHAR)                       AS code,
    CAST(Name AS VARCHAR)                       AS name,
    CAST(F_RefDate AS TIMESTAMP)                AS f_ref_date,
    CAST(T_RefDate AS TIMESTAMP)                AS t_ref_date,
    CAST(Category AS VARCHAR)                   AS category,
    CAST(Indicator AS VARCHAR)                  AS indicator,
    _company                              AS company,
    load_date
FROM latest
ORDER BY company, abs_entry
$seed$, $seed$Financial posting periods with their date ranges.$seed$, $seed${}$seed$::jsonb, $seed$$seed$, NOW(), :'workspace_id'::uuid),
($seed$sap_b1_oibt_latest$seed$, $seed$silver$seed$, $seed$sap_b1$seed$, $seed$["raw/sap_b1/OIBT"]$seed$::jsonb, $seed$
-- sap_b1_oibt_latest  (silver)  cartridge: sap_b1
-- sources: ["raw/sap_b1/OIBT"]
-- description: Batch quantity per item, BatchNum and warehouse (compatibility table).

WITH newest_run AS (
    -- Instantánea sin marca de agua: la corrida más reciente POR EMPRESA
    -- (todos sus lotes) y nada más. Deduplicar el histórico resucitaría
    -- filas que la fuente borró; quedarse con MAX(load_date) mezclaría dos
    -- corridas del mismo día.
    SELECT _company,
           arg_max(regexp_replace(_run_id, '-b[0-9]+$', ''), _extracted_at) AS _run_key
    FROM read_parquet('s3://{bucket}/raw/sap_b1/OIBT/**/*.parquet',
                      hive_partitioning = true,
                      union_by_name   = true)
    GROUP BY _company
),
latest AS (
    SELECT *
    FROM (
        SELECT s.*,
               ROW_NUMBER() OVER (PARTITION BY s._company, s.ItemCode, s.BatchNum, s.WhsCode ORDER BY s._extracted_at DESC) AS _rn
        FROM read_parquet('s3://{bucket}/raw/sap_b1/OIBT/**/*.parquet',
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
    CAST(BatchNum AS VARCHAR)                   AS batch_num,
    CAST(WhsCode AS VARCHAR)                    AS whs_code,
    CAST(Quantity AS DECIMAL(19,6))             AS quantity,
    _company                              AS company,
    load_date
FROM latest
ORDER BY company, item_code, batch_num, whs_code
$seed$, $seed$Batch quantity per item, BatchNum and warehouse (compatibility table).$seed$, $seed${}$seed$::jsonb, $seed$$seed$, NOW(), :'workspace_id'::uuid),
($seed$sap_b1_oinm_latest$seed$, $seed$silver$seed$, $seed$sap_b1$seed$, $seed$["raw/sap_b1/OINM"]$seed$::jsonb, $seed$
-- sap_b1_oinm_latest  (silver)  cartridge: sap_b1
-- sources: ["raw/sap_b1/OINM"]
-- description: Inventory transaction log (a view over OIVL/IVL1 in B1 >= 8.8); rows never change. One TransNum per document with one row per line (TransSeq): TransNum is the watermark, (TransNum, TransSeq) the page key.

WITH latest AS (
    -- Las filas nunca cambian en la fuente; el mismo registro puede llegar
    -- más de una vez (carga completa + incremental), así que se deduplica
    -- por clave sobre todo el histórico bronze.
    SELECT *
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
)
SELECT
    CAST(TransNum AS BIGINT)                    AS trans_num,
    CAST(TransSeq AS BIGINT)                    AS trans_seq,
    CAST(DocDate AS TIMESTAMP)                  AS doc_date,
    CAST(ItemCode AS VARCHAR)                   AS item_code,
    CAST(Warehouse AS VARCHAR)                  AS warehouse,
    CAST(InQty AS DECIMAL(19,6))                AS in_qty,
    CAST(OutQty AS DECIMAL(19,6))               AS out_qty,
    CAST(Price AS DECIMAL(19,6))                AS price,
    CAST(TransType AS BIGINT)                   AS trans_type,
    CAST(CreatedBy AS BIGINT)                   AS created_by,
    CAST(BASE_REF AS VARCHAR)                   AS base_ref,
    CAST(DocLineNum AS BIGINT)                  AS doc_line_num,
    CAST(Currency AS VARCHAR)                   AS currency,
    CAST(TransValue AS DECIMAL(19,6))           AS trans_value,
    CAST(CalcPrice AS DECIMAL(19,6))            AS calc_price,
    CAST(ApplObj AS VARCHAR)                    AS appl_obj,
    CAST(AppObjAbs AS BIGINT)                   AS app_obj_abs,
    CAST(CreateDate AS TIMESTAMP)               AS create_date,
    _company                              AS company,
    _source_updated_at                    AS source_updated_at,
    load_date
FROM latest
ORDER BY company, trans_num, trans_seq
$seed$, $seed$Inventory transaction log (a view over OIVL/IVL1 in B1 >= 8.8); rows never change. One TransNum per document with one row per line (TransSeq): TransNum is the watermark, (TransNum, TransSeq) the page key.$seed$, $seed${}$seed$::jsonb, $seed$$seed$, NOW(), :'workspace_id'::uuid),
($seed$sap_b1_oinv_latest$seed$, $seed$silver$seed$, $seed$sap_b1$seed$, $seed$["raw/sap_b1/OINV"]$seed$::jsonb, $seed$
-- sap_b1_oinv_latest  (silver)  cartridge: sap_b1
-- sources: ["raw/sap_b1/OINV"]
-- description: A/R invoices headers, ObjType 13: totals in document, local and system currency; CANCELED N/Y/C.

WITH latest AS (
    -- Estado ACTUAL por clave sobre TODO el histórico bronze. La entidad es
    -- incremental: quedarse con MAX(load_date) colapsaría la población al
    -- delta del día. Un borrado en la fuente no se refleja hasta una carga
    -- completa; ver README del cartucho.
    SELECT *
    FROM (
        SELECT *,
               ROW_NUMBER() OVER (
                   PARTITION BY _company, DocEntry
                   ORDER BY _source_updated_at DESC NULLS LAST, load_date DESC, _extracted_at DESC
               ) AS _rn
        FROM read_parquet('s3://{bucket}/raw/sap_b1/OINV/**/*.parquet',
                      hive_partitioning = true,
                      union_by_name   = true)
    )
    WHERE _rn = 1
)
SELECT
    CAST(DocEntry AS BIGINT)                    AS doc_entry,
    CAST(DocNum AS BIGINT)                      AS doc_num,
    CAST(DocType AS VARCHAR)                    AS doc_type,
    CAST(CANCELED AS VARCHAR)                   AS canceled,
    CAST(DocStatus AS VARCHAR)                  AS doc_status,
    CAST(ObjType AS VARCHAR)                    AS obj_type,
    CAST(DocDate AS TIMESTAMP)                  AS doc_date,
    CAST(DocDueDate AS TIMESTAMP)               AS doc_due_date,
    CAST(TaxDate AS TIMESTAMP)                  AS tax_date,
    CAST(CardCode AS VARCHAR)                   AS card_code,
    CAST(CardName AS VARCHAR)                   AS card_name,
    CAST(NumAtCard AS VARCHAR)                  AS num_at_card,
    CAST(DocCur AS VARCHAR)                     AS doc_cur,
    CAST(DocRate AS DECIMAL(19,6))              AS doc_rate,
    CAST(DocTotal AS DECIMAL(19,6))             AS doc_total,
    CAST(DocTotalFC AS DECIMAL(19,6))           AS doc_total_fc,
    CAST(DocTotalSy AS DECIMAL(19,6))           AS doc_total_sy,
    CAST(VatSum AS DECIMAL(19,6))               AS vat_sum,
    CAST(VatSumFC AS DECIMAL(19,6))             AS vat_sum_fc,
    CAST(VatSumSy AS DECIMAL(19,6))             AS vat_sum_sy,
    CAST(DiscSum AS DECIMAL(19,6))              AS disc_sum,
    CAST(GrosProfit AS DECIMAL(19,6))           AS gros_profit,
    CAST(GrosProfFC AS DECIMAL(19,6))           AS gros_prof_fc,
    CAST(GrosProfSy AS DECIMAL(19,6))           AS gros_prof_sy,
    CAST(SlpCode AS BIGINT)                     AS slp_code,
    CAST(GroupNum AS BIGINT)                    AS group_num,
    CAST(Comments AS VARCHAR)                   AS comments,
    CAST(TransId AS BIGINT)                     AS trans_id,
    CAST(BPLId AS BIGINT)                       AS bpl_id,
    CAST(Series AS BIGINT)                      AS series,
    CAST(CreateDate AS TIMESTAMP)               AS create_date,
    CAST(CreateTS AS BIGINT)                    AS create_ts,
    CAST(UpdateDate AS TIMESTAMP)               AS update_date,
    CAST(UpdateTS AS BIGINT)                    AS update_ts,
    CAST(UserSign AS BIGINT)                    AS user_sign,
    _company                              AS company,
    _source_updated_at                    AS source_updated_at,
    load_date
FROM latest
ORDER BY company, doc_entry
$seed$, $seed$A/R invoices headers, ObjType 13: totals in document, local and system currency; CANCELED N/Y/C.$seed$, $seed${}$seed$::jsonb, $seed$$seed$, NOW(), :'workspace_id'::uuid),
($seed$sap_b1_oitb_latest$seed$, $seed$silver$seed$, $seed$sap_b1$seed$, $seed$["raw/sap_b1/OITB"]$seed$::jsonb, $seed$
-- sap_b1_oitb_latest  (silver)  cartridge: sap_b1
-- sources: ["raw/sap_b1/OITB"]
-- description: Item groups.

WITH newest_run AS (
    -- Instantánea sin marca de agua: la corrida más reciente POR EMPRESA
    -- (todos sus lotes) y nada más. Deduplicar el histórico resucitaría
    -- filas que la fuente borró; quedarse con MAX(load_date) mezclaría dos
    -- corridas del mismo día.
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
$seed$, $seed$Item groups.$seed$, $seed${}$seed$::jsonb, $seed$$seed$, NOW(), :'workspace_id'::uuid),
($seed$sap_b1_oitm_latest$seed$, $seed$silver$seed$, $seed$sap_b1$seed$, $seed$["raw/sap_b1/OITM"]$seed$::jsonb, $seed$
-- sap_b1_oitm_latest  (silver)  cartridge: sap_b1
-- sources: ["raw/sap_b1/OITM"]
-- description: Item master: inventory / sales / purchase flags, batch management, default warehouse.

WITH latest AS (
    -- Estado ACTUAL por clave sobre TODO el histórico bronze. La entidad es
    -- incremental: quedarse con MAX(load_date) colapsaría la población al
    -- delta del día. Un borrado en la fuente no se refleja hasta una carga
    -- completa; ver README del cartucho.
    SELECT *
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
)
SELECT
    CAST(ItemCode AS VARCHAR)                   AS item_code,
    CAST(ItemName AS VARCHAR)                   AS item_name,
    CAST(ItmsGrpCod AS BIGINT)                  AS itms_grp_cod,
    CAST(InvntItem AS VARCHAR)                  AS invnt_item,
    CAST(SellItem AS VARCHAR)                   AS sell_item,
    CAST(PrchseItem AS VARCHAR)                 AS prchse_item,
    CAST(ManBtchNum AS VARCHAR)                 AS man_btch_num,
    CAST(DfltWH AS VARCHAR)                     AS dflt_wh,
    CAST(AvgPrice AS DECIMAL(19,6))             AS avg_price,
    CAST(LastPurPrc AS DECIMAL(19,6))           AS last_pur_prc,
    CAST(validFor AS VARCHAR)                   AS valid_for,
    CAST(frozenFor AS VARCHAR)                  AS frozen_for,
    CAST(CreateDate AS TIMESTAMP)               AS create_date,
    CAST(UpdateDate AS TIMESTAMP)               AS update_date,
    CAST(UpdateTS AS BIGINT)                    AS update_ts,
    _company                              AS company,
    _source_updated_at                    AS source_updated_at,
    load_date
FROM latest
ORDER BY company, item_code
$seed$, $seed$Item master: inventory / sales / purchase flags, batch management, default warehouse.$seed$, $seed${}$seed$::jsonb, $seed$$seed$, NOW(), :'workspace_id'::uuid),
($seed$sap_b1_oitt_latest$seed$, $seed$silver$seed$, $seed$sap_b1$seed$, $seed$["raw/sap_b1/OITT"]$seed$::jsonb, $seed$
-- sap_b1_oitt_latest  (silver)  cartridge: sap_b1
-- sources: ["raw/sap_b1/OITT"]
-- description: Bill of materials headers (production BOMs).

WITH latest AS (
    -- Estado ACTUAL por clave sobre TODO el histórico bronze. La entidad es
    -- incremental: quedarse con MAX(load_date) colapsaría la población al
    -- delta del día. Un borrado en la fuente no se refleja hasta una carga
    -- completa; ver README del cartucho.
    SELECT *
    FROM (
        SELECT *,
               ROW_NUMBER() OVER (
                   PARTITION BY _company, Code
                   ORDER BY _source_updated_at DESC NULLS LAST, load_date DESC, _extracted_at DESC
               ) AS _rn
        FROM read_parquet('s3://{bucket}/raw/sap_b1/OITT/**/*.parquet',
                      hive_partitioning = true,
                      union_by_name   = true)
    )
    WHERE _rn = 1
)
SELECT
    CAST(Code AS VARCHAR)                       AS code,
    CAST(TreeType AS VARCHAR)                   AS tree_type,
    CAST(Qauntity AS DECIMAL(19,6))             AS qauntity,
    CAST(ToWH AS VARCHAR)                       AS to_wh,
    CAST(PriceList AS BIGINT)                   AS price_list,
    CAST(CreateDate AS TIMESTAMP)               AS create_date,
    CAST(UpdateDate AS TIMESTAMP)               AS update_date,
    CAST(UpdateTS AS BIGINT)                    AS update_ts,
    _company                              AS company,
    _source_updated_at                    AS source_updated_at,
    load_date
FROM latest
ORDER BY company, code
$seed$, $seed$Bill of materials headers (production BOMs).$seed$, $seed${}$seed$::jsonb, $seed$$seed$, NOW(), :'workspace_id'::uuid),
($seed$sap_b1_oitw_latest$seed$, $seed$silver$seed$, $seed$sap_b1$seed$, $seed$["raw/sap_b1/OITW"]$seed$::jsonb, $seed$
-- sap_b1_oitw_latest  (silver)  cartridge: sap_b1
-- sources: ["raw/sap_b1/OITW"]
-- description: On-hand, committed and on-order quantities per item and warehouse (snapshot).

WITH newest_run AS (
    -- Instantánea sin marca de agua: la corrida más reciente POR EMPRESA
    -- (todos sus lotes) y nada más. Deduplicar el histórico resucitaría
    -- filas que la fuente borró; quedarse con MAX(load_date) mezclaría dos
    -- corridas del mismo día.
    SELECT _company,
           arg_max(regexp_replace(_run_id, '-b[0-9]+$', ''), _extracted_at) AS _run_key
    FROM read_parquet('s3://{bucket}/raw/sap_b1/OITW/**/*.parquet',
                      hive_partitioning = true,
                      union_by_name   = true)
    GROUP BY _company
),
latest AS (
    SELECT *
    FROM (
        SELECT s.*,
               ROW_NUMBER() OVER (PARTITION BY s._company, s.ItemCode, s.WhsCode ORDER BY s._extracted_at DESC) AS _rn
        FROM read_parquet('s3://{bucket}/raw/sap_b1/OITW/**/*.parquet',
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
    CAST(WhsCode AS VARCHAR)                    AS whs_code,
    CAST(OnHand AS DECIMAL(19,6))               AS on_hand,
    CAST(IsCommited AS DECIMAL(19,6))           AS is_commited,
    CAST(OnOrder AS DECIMAL(19,6))              AS on_order,
    CAST(AvgPrice AS DECIMAL(19,6))             AS avg_price,
    CAST(MinStock AS DECIMAL(19,6))             AS min_stock,
    CAST(MaxStock AS DECIMAL(19,6))             AS max_stock,
    _company                              AS company,
    load_date
FROM latest
ORDER BY company, item_code, whs_code
$seed$, $seed$On-hand, committed and on-order quantities per item and warehouse (snapshot).$seed$, $seed${}$seed$::jsonb, $seed$$seed$, NOW(), :'workspace_id'::uuid),
($seed$sap_b1_ojdt_latest$seed$, $seed$silver$seed$, $seed$sap_b1$seed$, $seed$["raw/sap_b1/OJDT"]$seed$::jsonb, $seed$
-- sap_b1_ojdt_latest  (silver)  cartridge: sap_b1
-- sources: ["raw/sap_b1/OJDT"]
-- description: Journal entry headers: RefDate, TransType (originating object), StornoToTr for reversals.

WITH latest AS (
    -- Estado ACTUAL por clave sobre TODO el histórico bronze. La entidad es
    -- incremental: quedarse con MAX(load_date) colapsaría la población al
    -- delta del día. Un borrado en la fuente no se refleja hasta una carga
    -- completa; ver README del cartucho.
    SELECT *
    FROM (
        SELECT *,
               ROW_NUMBER() OVER (
                   PARTITION BY _company, TransId
                   ORDER BY _source_updated_at DESC NULLS LAST, load_date DESC, _extracted_at DESC
               ) AS _rn
        FROM read_parquet('s3://{bucket}/raw/sap_b1/OJDT/**/*.parquet',
                      hive_partitioning = true,
                      union_by_name   = true)
    )
    WHERE _rn = 1
)
SELECT
    CAST(TransId AS BIGINT)                     AS trans_id,
    CAST(Number AS BIGINT)                      AS number,
    CAST(RefDate AS TIMESTAMP)                  AS ref_date,
    CAST(DueDate AS TIMESTAMP)                  AS due_date,
    CAST(TaxDate AS TIMESTAMP)                  AS tax_date,
    CAST(Memo AS VARCHAR)                       AS memo,
    CAST(TransType AS VARCHAR)                  AS trans_type,
    CAST(BaseRef AS VARCHAR)                    AS base_ref,
    CAST(CreatedBy AS BIGINT)                   AS created_by,
    CAST(StornoToTr AS BIGINT)                  AS storno_to_tr,
    CAST(CreateDate AS TIMESTAMP)               AS create_date,
    CAST(CreateTS AS BIGINT)                    AS create_ts,
    CAST(UpdateDate AS TIMESTAMP)               AS update_date,
    CAST(UpdateTS AS BIGINT)                    AS update_ts,
    _company                              AS company,
    _source_updated_at                    AS source_updated_at,
    load_date
FROM latest
ORDER BY company, trans_id
$seed$, $seed$Journal entry headers: RefDate, TransType (originating object), StornoToTr for reversals.$seed$, $seed${}$seed$::jsonb, $seed$$seed$, NOW(), :'workspace_id'::uuid),
($seed$sap_b1_opch_latest$seed$, $seed$silver$seed$, $seed$sap_b1$seed$, $seed$["raw/sap_b1/OPCH"]$seed$::jsonb, $seed$
-- sap_b1_opch_latest  (silver)  cartridge: sap_b1
-- sources: ["raw/sap_b1/OPCH"]
-- description: A/P invoices headers, ObjType 18: totals in document, local and system currency; CANCELED N/Y/C.

WITH latest AS (
    -- Estado ACTUAL por clave sobre TODO el histórico bronze. La entidad es
    -- incremental: quedarse con MAX(load_date) colapsaría la población al
    -- delta del día. Un borrado en la fuente no se refleja hasta una carga
    -- completa; ver README del cartucho.
    SELECT *
    FROM (
        SELECT *,
               ROW_NUMBER() OVER (
                   PARTITION BY _company, DocEntry
                   ORDER BY _source_updated_at DESC NULLS LAST, load_date DESC, _extracted_at DESC
               ) AS _rn
        FROM read_parquet('s3://{bucket}/raw/sap_b1/OPCH/**/*.parquet',
                      hive_partitioning = true,
                      union_by_name   = true)
    )
    WHERE _rn = 1
)
SELECT
    CAST(DocEntry AS BIGINT)                    AS doc_entry,
    CAST(DocNum AS BIGINT)                      AS doc_num,
    CAST(DocType AS VARCHAR)                    AS doc_type,
    CAST(CANCELED AS VARCHAR)                   AS canceled,
    CAST(DocStatus AS VARCHAR)                  AS doc_status,
    CAST(ObjType AS VARCHAR)                    AS obj_type,
    CAST(DocDate AS TIMESTAMP)                  AS doc_date,
    CAST(DocDueDate AS TIMESTAMP)               AS doc_due_date,
    CAST(TaxDate AS TIMESTAMP)                  AS tax_date,
    CAST(CardCode AS VARCHAR)                   AS card_code,
    CAST(CardName AS VARCHAR)                   AS card_name,
    CAST(NumAtCard AS VARCHAR)                  AS num_at_card,
    CAST(DocCur AS VARCHAR)                     AS doc_cur,
    CAST(DocRate AS DECIMAL(19,6))              AS doc_rate,
    CAST(DocTotal AS DECIMAL(19,6))             AS doc_total,
    CAST(DocTotalFC AS DECIMAL(19,6))           AS doc_total_fc,
    CAST(DocTotalSy AS DECIMAL(19,6))           AS doc_total_sy,
    CAST(VatSum AS DECIMAL(19,6))               AS vat_sum,
    CAST(VatSumFC AS DECIMAL(19,6))             AS vat_sum_fc,
    CAST(VatSumSy AS DECIMAL(19,6))             AS vat_sum_sy,
    CAST(DiscSum AS DECIMAL(19,6))              AS disc_sum,
    CAST(GrosProfit AS DECIMAL(19,6))           AS gros_profit,
    CAST(GrosProfFC AS DECIMAL(19,6))           AS gros_prof_fc,
    CAST(GrosProfSy AS DECIMAL(19,6))           AS gros_prof_sy,
    CAST(SlpCode AS BIGINT)                     AS slp_code,
    CAST(GroupNum AS BIGINT)                    AS group_num,
    CAST(Comments AS VARCHAR)                   AS comments,
    CAST(TransId AS BIGINT)                     AS trans_id,
    CAST(BPLId AS BIGINT)                       AS bpl_id,
    CAST(Series AS BIGINT)                      AS series,
    CAST(CreateDate AS TIMESTAMP)               AS create_date,
    CAST(CreateTS AS BIGINT)                    AS create_ts,
    CAST(UpdateDate AS TIMESTAMP)               AS update_date,
    CAST(UpdateTS AS BIGINT)                    AS update_ts,
    CAST(UserSign AS BIGINT)                    AS user_sign,
    _company                              AS company,
    _source_updated_at                    AS source_updated_at,
    load_date
FROM latest
ORDER BY company, doc_entry
$seed$, $seed$A/P invoices headers, ObjType 18: totals in document, local and system currency; CANCELED N/Y/C.$seed$, $seed${}$seed$::jsonb, $seed$$seed$, NOW(), :'workspace_id'::uuid),
($seed$sap_b1_opdn_latest$seed$, $seed$silver$seed$, $seed$sap_b1$seed$, $seed$["raw/sap_b1/OPDN"]$seed$::jsonb, $seed$
-- sap_b1_opdn_latest  (silver)  cartridge: sap_b1
-- sources: ["raw/sap_b1/OPDN"]
-- description: Goods receipt POs headers, ObjType 20: totals in document, local and system currency; CANCELED N/Y/C.

WITH latest AS (
    -- Estado ACTUAL por clave sobre TODO el histórico bronze. La entidad es
    -- incremental: quedarse con MAX(load_date) colapsaría la población al
    -- delta del día. Un borrado en la fuente no se refleja hasta una carga
    -- completa; ver README del cartucho.
    SELECT *
    FROM (
        SELECT *,
               ROW_NUMBER() OVER (
                   PARTITION BY _company, DocEntry
                   ORDER BY _source_updated_at DESC NULLS LAST, load_date DESC, _extracted_at DESC
               ) AS _rn
        FROM read_parquet('s3://{bucket}/raw/sap_b1/OPDN/**/*.parquet',
                      hive_partitioning = true,
                      union_by_name   = true)
    )
    WHERE _rn = 1
)
SELECT
    CAST(DocEntry AS BIGINT)                    AS doc_entry,
    CAST(DocNum AS BIGINT)                      AS doc_num,
    CAST(DocType AS VARCHAR)                    AS doc_type,
    CAST(CANCELED AS VARCHAR)                   AS canceled,
    CAST(DocStatus AS VARCHAR)                  AS doc_status,
    CAST(ObjType AS VARCHAR)                    AS obj_type,
    CAST(DocDate AS TIMESTAMP)                  AS doc_date,
    CAST(DocDueDate AS TIMESTAMP)               AS doc_due_date,
    CAST(TaxDate AS TIMESTAMP)                  AS tax_date,
    CAST(CardCode AS VARCHAR)                   AS card_code,
    CAST(CardName AS VARCHAR)                   AS card_name,
    CAST(NumAtCard AS VARCHAR)                  AS num_at_card,
    CAST(DocCur AS VARCHAR)                     AS doc_cur,
    CAST(DocRate AS DECIMAL(19,6))              AS doc_rate,
    CAST(DocTotal AS DECIMAL(19,6))             AS doc_total,
    CAST(DocTotalFC AS DECIMAL(19,6))           AS doc_total_fc,
    CAST(DocTotalSy AS DECIMAL(19,6))           AS doc_total_sy,
    CAST(VatSum AS DECIMAL(19,6))               AS vat_sum,
    CAST(VatSumFC AS DECIMAL(19,6))             AS vat_sum_fc,
    CAST(VatSumSy AS DECIMAL(19,6))             AS vat_sum_sy,
    CAST(DiscSum AS DECIMAL(19,6))              AS disc_sum,
    CAST(GrosProfit AS DECIMAL(19,6))           AS gros_profit,
    CAST(GrosProfFC AS DECIMAL(19,6))           AS gros_prof_fc,
    CAST(GrosProfSy AS DECIMAL(19,6))           AS gros_prof_sy,
    CAST(SlpCode AS BIGINT)                     AS slp_code,
    CAST(GroupNum AS BIGINT)                    AS group_num,
    CAST(Comments AS VARCHAR)                   AS comments,
    CAST(TransId AS BIGINT)                     AS trans_id,
    CAST(BPLId AS BIGINT)                       AS bpl_id,
    CAST(Series AS BIGINT)                      AS series,
    CAST(CreateDate AS TIMESTAMP)               AS create_date,
    CAST(CreateTS AS BIGINT)                    AS create_ts,
    CAST(UpdateDate AS TIMESTAMP)               AS update_date,
    CAST(UpdateTS AS BIGINT)                    AS update_ts,
    CAST(UserSign AS BIGINT)                    AS user_sign,
    _company                              AS company,
    _source_updated_at                    AS source_updated_at,
    load_date
FROM latest
ORDER BY company, doc_entry
$seed$, $seed$Goods receipt POs headers, ObjType 20: totals in document, local and system currency; CANCELED N/Y/C.$seed$, $seed${}$seed$::jsonb, $seed$$seed$, NOW(), :'workspace_id'::uuid),
($seed$sap_b1_opor_latest$seed$, $seed$silver$seed$, $seed$sap_b1$seed$, $seed$["raw/sap_b1/OPOR"]$seed$::jsonb, $seed$
-- sap_b1_opor_latest  (silver)  cartridge: sap_b1
-- sources: ["raw/sap_b1/OPOR"]
-- description: Purchase orders headers, ObjType 22: totals in document, local and system currency; CANCELED N/Y/C.

WITH latest AS (
    -- Estado ACTUAL por clave sobre TODO el histórico bronze. La entidad es
    -- incremental: quedarse con MAX(load_date) colapsaría la población al
    -- delta del día. Un borrado en la fuente no se refleja hasta una carga
    -- completa; ver README del cartucho.
    SELECT *
    FROM (
        SELECT *,
               ROW_NUMBER() OVER (
                   PARTITION BY _company, DocEntry
                   ORDER BY _source_updated_at DESC NULLS LAST, load_date DESC, _extracted_at DESC
               ) AS _rn
        FROM read_parquet('s3://{bucket}/raw/sap_b1/OPOR/**/*.parquet',
                      hive_partitioning = true,
                      union_by_name   = true)
    )
    WHERE _rn = 1
)
SELECT
    CAST(DocEntry AS BIGINT)                    AS doc_entry,
    CAST(DocNum AS BIGINT)                      AS doc_num,
    CAST(DocType AS VARCHAR)                    AS doc_type,
    CAST(CANCELED AS VARCHAR)                   AS canceled,
    CAST(DocStatus AS VARCHAR)                  AS doc_status,
    CAST(ObjType AS VARCHAR)                    AS obj_type,
    CAST(DocDate AS TIMESTAMP)                  AS doc_date,
    CAST(DocDueDate AS TIMESTAMP)               AS doc_due_date,
    CAST(TaxDate AS TIMESTAMP)                  AS tax_date,
    CAST(CardCode AS VARCHAR)                   AS card_code,
    CAST(CardName AS VARCHAR)                   AS card_name,
    CAST(NumAtCard AS VARCHAR)                  AS num_at_card,
    CAST(DocCur AS VARCHAR)                     AS doc_cur,
    CAST(DocRate AS DECIMAL(19,6))              AS doc_rate,
    CAST(DocTotal AS DECIMAL(19,6))             AS doc_total,
    CAST(DocTotalFC AS DECIMAL(19,6))           AS doc_total_fc,
    CAST(DocTotalSy AS DECIMAL(19,6))           AS doc_total_sy,
    CAST(VatSum AS DECIMAL(19,6))               AS vat_sum,
    CAST(VatSumFC AS DECIMAL(19,6))             AS vat_sum_fc,
    CAST(VatSumSy AS DECIMAL(19,6))             AS vat_sum_sy,
    CAST(DiscSum AS DECIMAL(19,6))              AS disc_sum,
    CAST(GrosProfit AS DECIMAL(19,6))           AS gros_profit,
    CAST(GrosProfFC AS DECIMAL(19,6))           AS gros_prof_fc,
    CAST(GrosProfSy AS DECIMAL(19,6))           AS gros_prof_sy,
    CAST(SlpCode AS BIGINT)                     AS slp_code,
    CAST(GroupNum AS BIGINT)                    AS group_num,
    CAST(Comments AS VARCHAR)                   AS comments,
    CAST(TransId AS BIGINT)                     AS trans_id,
    CAST(BPLId AS BIGINT)                       AS bpl_id,
    CAST(Series AS BIGINT)                      AS series,
    CAST(CreateDate AS TIMESTAMP)               AS create_date,
    CAST(CreateTS AS BIGINT)                    AS create_ts,
    CAST(UpdateDate AS TIMESTAMP)               AS update_date,
    CAST(UpdateTS AS BIGINT)                    AS update_ts,
    CAST(UserSign AS BIGINT)                    AS user_sign,
    _company                              AS company,
    _source_updated_at                    AS source_updated_at,
    load_date
FROM latest
ORDER BY company, doc_entry
$seed$, $seed$Purchase orders headers, ObjType 22: totals in document, local and system currency; CANCELED N/Y/C.$seed$, $seed${}$seed$::jsonb, $seed$$seed$, NOW(), :'workspace_id'::uuid),
($seed$sap_b1_oprc_latest$seed$, $seed$silver$seed$, $seed$sap_b1$seed$, $seed$["raw/sap_b1/OPRC"]$seed$::jsonb, $seed$
-- sap_b1_oprc_latest  (silver)  cartridge: sap_b1
-- sources: ["raw/sap_b1/OPRC"]
-- description: Profit centres / cost centres (OcrCode dimension).

WITH newest_run AS (
    -- Instantánea sin marca de agua: la corrida más reciente POR EMPRESA
    -- (todos sus lotes) y nada más. Deduplicar el histórico resucitaría
    -- filas que la fuente borró; quedarse con MAX(load_date) mezclaría dos
    -- corridas del mismo día.
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
$seed$, $seed$Profit centres / cost centres (OcrCode dimension).$seed$, $seed${}$seed$::jsonb, $seed$$seed$, NOW(), :'workspace_id'::uuid),
($seed$sap_b1_ordn_latest$seed$, $seed$silver$seed$, $seed$sap_b1$seed$, $seed$["raw/sap_b1/ORDN"]$seed$::jsonb, $seed$
-- sap_b1_ordn_latest  (silver)  cartridge: sap_b1
-- sources: ["raw/sap_b1/ORDN"]
-- description: Returns headers, ObjType 16: totals in document, local and system currency; CANCELED N/Y/C.

WITH latest AS (
    -- Estado ACTUAL por clave sobre TODO el histórico bronze. La entidad es
    -- incremental: quedarse con MAX(load_date) colapsaría la población al
    -- delta del día. Un borrado en la fuente no se refleja hasta una carga
    -- completa; ver README del cartucho.
    SELECT *
    FROM (
        SELECT *,
               ROW_NUMBER() OVER (
                   PARTITION BY _company, DocEntry
                   ORDER BY _source_updated_at DESC NULLS LAST, load_date DESC, _extracted_at DESC
               ) AS _rn
        FROM read_parquet('s3://{bucket}/raw/sap_b1/ORDN/**/*.parquet',
                      hive_partitioning = true,
                      union_by_name   = true)
    )
    WHERE _rn = 1
)
SELECT
    CAST(DocEntry AS BIGINT)                    AS doc_entry,
    CAST(DocNum AS BIGINT)                      AS doc_num,
    CAST(DocType AS VARCHAR)                    AS doc_type,
    CAST(CANCELED AS VARCHAR)                   AS canceled,
    CAST(DocStatus AS VARCHAR)                  AS doc_status,
    CAST(ObjType AS VARCHAR)                    AS obj_type,
    CAST(DocDate AS TIMESTAMP)                  AS doc_date,
    CAST(DocDueDate AS TIMESTAMP)               AS doc_due_date,
    CAST(TaxDate AS TIMESTAMP)                  AS tax_date,
    CAST(CardCode AS VARCHAR)                   AS card_code,
    CAST(CardName AS VARCHAR)                   AS card_name,
    CAST(NumAtCard AS VARCHAR)                  AS num_at_card,
    CAST(DocCur AS VARCHAR)                     AS doc_cur,
    CAST(DocRate AS DECIMAL(19,6))              AS doc_rate,
    CAST(DocTotal AS DECIMAL(19,6))             AS doc_total,
    CAST(DocTotalFC AS DECIMAL(19,6))           AS doc_total_fc,
    CAST(DocTotalSy AS DECIMAL(19,6))           AS doc_total_sy,
    CAST(VatSum AS DECIMAL(19,6))               AS vat_sum,
    CAST(VatSumFC AS DECIMAL(19,6))             AS vat_sum_fc,
    CAST(VatSumSy AS DECIMAL(19,6))             AS vat_sum_sy,
    CAST(DiscSum AS DECIMAL(19,6))              AS disc_sum,
    CAST(GrosProfit AS DECIMAL(19,6))           AS gros_profit,
    CAST(GrosProfFC AS DECIMAL(19,6))           AS gros_prof_fc,
    CAST(GrosProfSy AS DECIMAL(19,6))           AS gros_prof_sy,
    CAST(SlpCode AS BIGINT)                     AS slp_code,
    CAST(GroupNum AS BIGINT)                    AS group_num,
    CAST(Comments AS VARCHAR)                   AS comments,
    CAST(TransId AS BIGINT)                     AS trans_id,
    CAST(BPLId AS BIGINT)                       AS bpl_id,
    CAST(Series AS BIGINT)                      AS series,
    CAST(CreateDate AS TIMESTAMP)               AS create_date,
    CAST(CreateTS AS BIGINT)                    AS create_ts,
    CAST(UpdateDate AS TIMESTAMP)               AS update_date,
    CAST(UpdateTS AS BIGINT)                    AS update_ts,
    CAST(UserSign AS BIGINT)                    AS user_sign,
    _company                              AS company,
    _source_updated_at                    AS source_updated_at,
    load_date
FROM latest
ORDER BY company, doc_entry
$seed$, $seed$Returns headers, ObjType 16: totals in document, local and system currency; CANCELED N/Y/C.$seed$, $seed${}$seed$::jsonb, $seed$$seed$, NOW(), :'workspace_id'::uuid),
($seed$sap_b1_ordr_latest$seed$, $seed$silver$seed$, $seed$sap_b1$seed$, $seed$["raw/sap_b1/ORDR"]$seed$::jsonb, $seed$
-- sap_b1_ordr_latest  (silver)  cartridge: sap_b1
-- sources: ["raw/sap_b1/ORDR"]
-- description: Sales orders headers, ObjType 17: totals in document, local and system currency; CANCELED N/Y/C.

WITH latest AS (
    -- Estado ACTUAL por clave sobre TODO el histórico bronze. La entidad es
    -- incremental: quedarse con MAX(load_date) colapsaría la población al
    -- delta del día. Un borrado en la fuente no se refleja hasta una carga
    -- completa; ver README del cartucho.
    SELECT *
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
)
SELECT
    CAST(DocEntry AS BIGINT)                    AS doc_entry,
    CAST(DocNum AS BIGINT)                      AS doc_num,
    CAST(DocType AS VARCHAR)                    AS doc_type,
    CAST(CANCELED AS VARCHAR)                   AS canceled,
    CAST(DocStatus AS VARCHAR)                  AS doc_status,
    CAST(ObjType AS VARCHAR)                    AS obj_type,
    CAST(DocDate AS TIMESTAMP)                  AS doc_date,
    CAST(DocDueDate AS TIMESTAMP)               AS doc_due_date,
    CAST(TaxDate AS TIMESTAMP)                  AS tax_date,
    CAST(CardCode AS VARCHAR)                   AS card_code,
    CAST(CardName AS VARCHAR)                   AS card_name,
    CAST(NumAtCard AS VARCHAR)                  AS num_at_card,
    CAST(DocCur AS VARCHAR)                     AS doc_cur,
    CAST(DocRate AS DECIMAL(19,6))              AS doc_rate,
    CAST(DocTotal AS DECIMAL(19,6))             AS doc_total,
    CAST(DocTotalFC AS DECIMAL(19,6))           AS doc_total_fc,
    CAST(DocTotalSy AS DECIMAL(19,6))           AS doc_total_sy,
    CAST(VatSum AS DECIMAL(19,6))               AS vat_sum,
    CAST(VatSumFC AS DECIMAL(19,6))             AS vat_sum_fc,
    CAST(VatSumSy AS DECIMAL(19,6))             AS vat_sum_sy,
    CAST(DiscSum AS DECIMAL(19,6))              AS disc_sum,
    CAST(GrosProfit AS DECIMAL(19,6))           AS gros_profit,
    CAST(GrosProfFC AS DECIMAL(19,6))           AS gros_prof_fc,
    CAST(GrosProfSy AS DECIMAL(19,6))           AS gros_prof_sy,
    CAST(SlpCode AS BIGINT)                     AS slp_code,
    CAST(GroupNum AS BIGINT)                    AS group_num,
    CAST(Comments AS VARCHAR)                   AS comments,
    CAST(TransId AS BIGINT)                     AS trans_id,
    CAST(BPLId AS BIGINT)                       AS bpl_id,
    CAST(Series AS BIGINT)                      AS series,
    CAST(CreateDate AS TIMESTAMP)               AS create_date,
    CAST(CreateTS AS BIGINT)                    AS create_ts,
    CAST(UpdateDate AS TIMESTAMP)               AS update_date,
    CAST(UpdateTS AS BIGINT)                    AS update_ts,
    CAST(UserSign AS BIGINT)                    AS user_sign,
    _company                              AS company,
    _source_updated_at                    AS source_updated_at,
    load_date
FROM latest
ORDER BY company, doc_entry
$seed$, $seed$Sales orders headers, ObjType 17: totals in document, local and system currency; CANCELED N/Y/C.$seed$, $seed${}$seed$::jsonb, $seed$$seed$, NOW(), :'workspace_id'::uuid),
($seed$sap_b1_orin_latest$seed$, $seed$silver$seed$, $seed$sap_b1$seed$, $seed$["raw/sap_b1/ORIN"]$seed$::jsonb, $seed$
-- sap_b1_orin_latest  (silver)  cartridge: sap_b1
-- sources: ["raw/sap_b1/ORIN"]
-- description: A/R credit memos headers, ObjType 14: totals in document, local and system currency; CANCELED N/Y/C.

WITH latest AS (
    -- Estado ACTUAL por clave sobre TODO el histórico bronze. La entidad es
    -- incremental: quedarse con MAX(load_date) colapsaría la población al
    -- delta del día. Un borrado en la fuente no se refleja hasta una carga
    -- completa; ver README del cartucho.
    SELECT *
    FROM (
        SELECT *,
               ROW_NUMBER() OVER (
                   PARTITION BY _company, DocEntry
                   ORDER BY _source_updated_at DESC NULLS LAST, load_date DESC, _extracted_at DESC
               ) AS _rn
        FROM read_parquet('s3://{bucket}/raw/sap_b1/ORIN/**/*.parquet',
                      hive_partitioning = true,
                      union_by_name   = true)
    )
    WHERE _rn = 1
)
SELECT
    CAST(DocEntry AS BIGINT)                    AS doc_entry,
    CAST(DocNum AS BIGINT)                      AS doc_num,
    CAST(DocType AS VARCHAR)                    AS doc_type,
    CAST(CANCELED AS VARCHAR)                   AS canceled,
    CAST(DocStatus AS VARCHAR)                  AS doc_status,
    CAST(ObjType AS VARCHAR)                    AS obj_type,
    CAST(DocDate AS TIMESTAMP)                  AS doc_date,
    CAST(DocDueDate AS TIMESTAMP)               AS doc_due_date,
    CAST(TaxDate AS TIMESTAMP)                  AS tax_date,
    CAST(CardCode AS VARCHAR)                   AS card_code,
    CAST(CardName AS VARCHAR)                   AS card_name,
    CAST(NumAtCard AS VARCHAR)                  AS num_at_card,
    CAST(DocCur AS VARCHAR)                     AS doc_cur,
    CAST(DocRate AS DECIMAL(19,6))              AS doc_rate,
    CAST(DocTotal AS DECIMAL(19,6))             AS doc_total,
    CAST(DocTotalFC AS DECIMAL(19,6))           AS doc_total_fc,
    CAST(DocTotalSy AS DECIMAL(19,6))           AS doc_total_sy,
    CAST(VatSum AS DECIMAL(19,6))               AS vat_sum,
    CAST(VatSumFC AS DECIMAL(19,6))             AS vat_sum_fc,
    CAST(VatSumSy AS DECIMAL(19,6))             AS vat_sum_sy,
    CAST(DiscSum AS DECIMAL(19,6))              AS disc_sum,
    CAST(GrosProfit AS DECIMAL(19,6))           AS gros_profit,
    CAST(GrosProfFC AS DECIMAL(19,6))           AS gros_prof_fc,
    CAST(GrosProfSy AS DECIMAL(19,6))           AS gros_prof_sy,
    CAST(SlpCode AS BIGINT)                     AS slp_code,
    CAST(GroupNum AS BIGINT)                    AS group_num,
    CAST(Comments AS VARCHAR)                   AS comments,
    CAST(TransId AS BIGINT)                     AS trans_id,
    CAST(BPLId AS BIGINT)                       AS bpl_id,
    CAST(Series AS BIGINT)                      AS series,
    CAST(CreateDate AS TIMESTAMP)               AS create_date,
    CAST(CreateTS AS BIGINT)                    AS create_ts,
    CAST(UpdateDate AS TIMESTAMP)               AS update_date,
    CAST(UpdateTS AS BIGINT)                    AS update_ts,
    CAST(UserSign AS BIGINT)                    AS user_sign,
    _company                              AS company,
    _source_updated_at                    AS source_updated_at,
    load_date
FROM latest
ORDER BY company, doc_entry
$seed$, $seed$A/R credit memos headers, ObjType 14: totals in document, local and system currency; CANCELED N/Y/C.$seed$, $seed${}$seed$::jsonb, $seed$$seed$, NOW(), :'workspace_id'::uuid),
($seed$sap_b1_orpc_latest$seed$, $seed$silver$seed$, $seed$sap_b1$seed$, $seed$["raw/sap_b1/ORPC"]$seed$::jsonb, $seed$
-- sap_b1_orpc_latest  (silver)  cartridge: sap_b1
-- sources: ["raw/sap_b1/ORPC"]
-- description: A/P credit memos headers, ObjType 19: totals in document, local and system currency; CANCELED N/Y/C.

WITH latest AS (
    -- Estado ACTUAL por clave sobre TODO el histórico bronze. La entidad es
    -- incremental: quedarse con MAX(load_date) colapsaría la población al
    -- delta del día. Un borrado en la fuente no se refleja hasta una carga
    -- completa; ver README del cartucho.
    SELECT *
    FROM (
        SELECT *,
               ROW_NUMBER() OVER (
                   PARTITION BY _company, DocEntry
                   ORDER BY _source_updated_at DESC NULLS LAST, load_date DESC, _extracted_at DESC
               ) AS _rn
        FROM read_parquet('s3://{bucket}/raw/sap_b1/ORPC/**/*.parquet',
                      hive_partitioning = true,
                      union_by_name   = true)
    )
    WHERE _rn = 1
)
SELECT
    CAST(DocEntry AS BIGINT)                    AS doc_entry,
    CAST(DocNum AS BIGINT)                      AS doc_num,
    CAST(DocType AS VARCHAR)                    AS doc_type,
    CAST(CANCELED AS VARCHAR)                   AS canceled,
    CAST(DocStatus AS VARCHAR)                  AS doc_status,
    CAST(ObjType AS VARCHAR)                    AS obj_type,
    CAST(DocDate AS TIMESTAMP)                  AS doc_date,
    CAST(DocDueDate AS TIMESTAMP)               AS doc_due_date,
    CAST(TaxDate AS TIMESTAMP)                  AS tax_date,
    CAST(CardCode AS VARCHAR)                   AS card_code,
    CAST(CardName AS VARCHAR)                   AS card_name,
    CAST(NumAtCard AS VARCHAR)                  AS num_at_card,
    CAST(DocCur AS VARCHAR)                     AS doc_cur,
    CAST(DocRate AS DECIMAL(19,6))              AS doc_rate,
    CAST(DocTotal AS DECIMAL(19,6))             AS doc_total,
    CAST(DocTotalFC AS DECIMAL(19,6))           AS doc_total_fc,
    CAST(DocTotalSy AS DECIMAL(19,6))           AS doc_total_sy,
    CAST(VatSum AS DECIMAL(19,6))               AS vat_sum,
    CAST(VatSumFC AS DECIMAL(19,6))             AS vat_sum_fc,
    CAST(VatSumSy AS DECIMAL(19,6))             AS vat_sum_sy,
    CAST(DiscSum AS DECIMAL(19,6))              AS disc_sum,
    CAST(GrosProfit AS DECIMAL(19,6))           AS gros_profit,
    CAST(GrosProfFC AS DECIMAL(19,6))           AS gros_prof_fc,
    CAST(GrosProfSy AS DECIMAL(19,6))           AS gros_prof_sy,
    CAST(SlpCode AS BIGINT)                     AS slp_code,
    CAST(GroupNum AS BIGINT)                    AS group_num,
    CAST(Comments AS VARCHAR)                   AS comments,
    CAST(TransId AS BIGINT)                     AS trans_id,
    CAST(BPLId AS BIGINT)                       AS bpl_id,
    CAST(Series AS BIGINT)                      AS series,
    CAST(CreateDate AS TIMESTAMP)               AS create_date,
    CAST(CreateTS AS BIGINT)                    AS create_ts,
    CAST(UpdateDate AS TIMESTAMP)               AS update_date,
    CAST(UpdateTS AS BIGINT)                    AS update_ts,
    CAST(UserSign AS BIGINT)                    AS user_sign,
    _company                              AS company,
    _source_updated_at                    AS source_updated_at,
    load_date
FROM latest
ORDER BY company, doc_entry
$seed$, $seed$A/P credit memos headers, ObjType 19: totals in document, local and system currency; CANCELED N/Y/C.$seed$, $seed${}$seed$::jsonb, $seed$$seed$, NOW(), :'workspace_id'::uuid),
($seed$sap_b1_ortt_latest$seed$, $seed$silver$seed$, $seed$sap_b1$seed$, $seed$["raw/sap_b1/ORTT"]$seed$::jsonb, $seed$
-- sap_b1_ortt_latest  (silver)  cartridge: sap_b1
-- sources: ["raw/sap_b1/ORTT"]
-- description: Daily exchange rates per currency (RateDate, Currency).

WITH newest_run AS (
    -- Instantánea sin marca de agua: la corrida más reciente POR EMPRESA
    -- (todos sus lotes) y nada más. Deduplicar el histórico resucitaría
    -- filas que la fuente borró; quedarse con MAX(load_date) mezclaría dos
    -- corridas del mismo día.
    SELECT _company,
           arg_max(regexp_replace(_run_id, '-b[0-9]+$', ''), _extracted_at) AS _run_key
    FROM read_parquet('s3://{bucket}/raw/sap_b1/ORTT/**/*.parquet',
                      hive_partitioning = true,
                      union_by_name   = true)
    GROUP BY _company
),
latest AS (
    SELECT *
    FROM (
        SELECT s.*,
               ROW_NUMBER() OVER (PARTITION BY s._company, s.RateDate, s.Currency ORDER BY s._extracted_at DESC) AS _rn
        FROM read_parquet('s3://{bucket}/raw/sap_b1/ORTT/**/*.parquet',
                      hive_partitioning = true,
                      union_by_name   = true) s
        JOIN newest_run n
          ON n._company = s._company
         AND regexp_replace(s._run_id, '-b[0-9]+$', '') = n._run_key
    )
    WHERE _rn = 1
)
SELECT
    CAST(RateDate AS TIMESTAMP)                 AS rate_date,
    CAST(Currency AS VARCHAR)                   AS currency,
    CAST(Rate AS DECIMAL(19,6))                 AS rate,
    _company                              AS company,
    load_date
FROM latest
ORDER BY company, rate_date, currency
$seed$, $seed$Daily exchange rates per currency (RateDate, Currency).$seed$, $seed${}$seed$::jsonb, $seed$$seed$, NOW(), :'workspace_id'::uuid),
($seed$sap_b1_oslp_latest$seed$, $seed$silver$seed$, $seed$sap_b1$seed$, $seed$["raw/sap_b1/OSLP"]$seed$::jsonb, $seed$
-- sap_b1_oslp_latest  (silver)  cartridge: sap_b1
-- sources: ["raw/sap_b1/OSLP"]
-- description: Sales employees / buyers referenced by SlpCode.

WITH newest_run AS (
    -- Instantánea sin marca de agua: la corrida más reciente POR EMPRESA
    -- (todos sus lotes) y nada más. Deduplicar el histórico resucitaría
    -- filas que la fuente borró; quedarse con MAX(load_date) mezclaría dos
    -- corridas del mismo día.
    SELECT _company,
           arg_max(regexp_replace(_run_id, '-b[0-9]+$', ''), _extracted_at) AS _run_key
    FROM read_parquet('s3://{bucket}/raw/sap_b1/OSLP/**/*.parquet',
                      hive_partitioning = true,
                      union_by_name   = true)
    GROUP BY _company
),
latest AS (
    SELECT *
    FROM (
        SELECT s.*,
               ROW_NUMBER() OVER (PARTITION BY s._company, s.SlpCode ORDER BY s._extracted_at DESC) AS _rn
        FROM read_parquet('s3://{bucket}/raw/sap_b1/OSLP/**/*.parquet',
                      hive_partitioning = true,
                      union_by_name   = true) s
        JOIN newest_run n
          ON n._company = s._company
         AND regexp_replace(s._run_id, '-b[0-9]+$', '') = n._run_key
    )
    WHERE _rn = 1
)
SELECT
    CAST(SlpCode AS BIGINT)                     AS slp_code,
    CAST(SlpName AS VARCHAR)                    AS slp_name,
    CAST(Active AS VARCHAR)                     AS active,
    _company                              AS company,
    load_date
FROM latest
ORDER BY company, slp_code
$seed$, $seed$Sales employees / buyers referenced by SlpCode.$seed$, $seed${}$seed$::jsonb, $seed$$seed$, NOW(), :'workspace_id'::uuid),
($seed$sap_b1_owhs_latest$seed$, $seed$silver$seed$, $seed$sap_b1$seed$, $seed$["raw/sap_b1/OWHS"]$seed$::jsonb, $seed$
-- sap_b1_owhs_latest  (silver)  cartridge: sap_b1
-- sources: ["raw/sap_b1/OWHS"]
-- description: Warehouse codes and names.

WITH newest_run AS (
    -- Instantánea sin marca de agua: la corrida más reciente POR EMPRESA
    -- (todos sus lotes) y nada más. Deduplicar el histórico resucitaría
    -- filas que la fuente borró; quedarse con MAX(load_date) mezclaría dos
    -- corridas del mismo día.
    SELECT _company,
           arg_max(regexp_replace(_run_id, '-b[0-9]+$', ''), _extracted_at) AS _run_key
    FROM read_parquet('s3://{bucket}/raw/sap_b1/OWHS/**/*.parquet',
                      hive_partitioning = true,
                      union_by_name   = true)
    GROUP BY _company
),
latest AS (
    SELECT *
    FROM (
        SELECT s.*,
               ROW_NUMBER() OVER (PARTITION BY s._company, s.WhsCode ORDER BY s._extracted_at DESC) AS _rn
        FROM read_parquet('s3://{bucket}/raw/sap_b1/OWHS/**/*.parquet',
                      hive_partitioning = true,
                      union_by_name   = true) s
        JOIN newest_run n
          ON n._company = s._company
         AND regexp_replace(s._run_id, '-b[0-9]+$', '') = n._run_key
    )
    WHERE _rn = 1
)
SELECT
    CAST(WhsCode AS VARCHAR)                    AS whs_code,
    CAST(WhsName AS VARCHAR)                    AS whs_name,
    CAST(Locked AS VARCHAR)                     AS locked,
    _company                              AS company,
    load_date
FROM latest
ORDER BY company, whs_code
$seed$, $seed$Warehouse codes and names.$seed$, $seed${}$seed$::jsonb, $seed$$seed$, NOW(), :'workspace_id'::uuid),
($seed$sap_b1_owor_latest$seed$, $seed$silver$seed$, $seed$sap_b1$seed$, $seed$["raw/sap_b1/OWOR"]$seed$::jsonb, $seed$
-- sap_b1_owor_latest  (silver)  cartridge: sap_b1
-- sources: ["raw/sap_b1/OWOR"]
-- description: Production order headers: planned, completed and rejected quantities, status and dates.

WITH latest AS (
    -- Estado ACTUAL por clave sobre TODO el histórico bronze. La entidad es
    -- incremental: quedarse con MAX(load_date) colapsaría la población al
    -- delta del día. Un borrado en la fuente no se refleja hasta una carga
    -- completa; ver README del cartucho.
    SELECT *
    FROM (
        SELECT *,
               ROW_NUMBER() OVER (
                   PARTITION BY _company, DocEntry
                   ORDER BY _source_updated_at DESC NULLS LAST, load_date DESC, _extracted_at DESC
               ) AS _rn
        FROM read_parquet('s3://{bucket}/raw/sap_b1/OWOR/**/*.parquet',
                      hive_partitioning = true,
                      union_by_name   = true)
    )
    WHERE _rn = 1
)
SELECT
    CAST(DocEntry AS BIGINT)                    AS doc_entry,
    CAST(DocNum AS BIGINT)                      AS doc_num,
    CAST(ItemCode AS VARCHAR)                   AS item_code,
    CAST(Status AS VARCHAR)                     AS status,
    CAST(Type AS VARCHAR)                       AS type,
    CAST(PlannedQty AS DECIMAL(19,6))           AS planned_qty,
    CAST(CmpltQty AS DECIMAL(19,6))             AS cmplt_qty,
    CAST(RjctQty AS DECIMAL(19,6))              AS rjct_qty,
    CAST(PostDate AS TIMESTAMP)                 AS post_date,
    CAST(DueDate AS TIMESTAMP)                  AS due_date,
    CAST(StartDate AS TIMESTAMP)                AS start_date,
    CAST(CloseDate AS TIMESTAMP)                AS close_date,
    CAST(Warehouse AS VARCHAR)                  AS warehouse,
    CAST(CreateDate AS TIMESTAMP)               AS create_date,
    CAST(CreateTS AS BIGINT)                    AS create_ts,
    CAST(UpdateDate AS TIMESTAMP)               AS update_date,
    CAST(UpdateTS AS BIGINT)                    AS update_ts,
    _company                              AS company,
    _source_updated_at                    AS source_updated_at,
    load_date
FROM latest
ORDER BY company, doc_entry
$seed$, $seed$Production order headers: planned, completed and rejected quantities, status and dates.$seed$, $seed${}$seed$::jsonb, $seed$$seed$, NOW(), :'workspace_id'::uuid),
($seed$sap_b1_owtr_latest$seed$, $seed$silver$seed$, $seed$sap_b1$seed$, $seed$["raw/sap_b1/OWTR"]$seed$::jsonb, $seed$
-- sap_b1_owtr_latest  (silver)  cartridge: sap_b1
-- sources: ["raw/sap_b1/OWTR"]
-- description: Inventory transfer headers, ObjType 67: source warehouse (Filler) and target warehouse (ToWhsCode); stock moves, money does not.

WITH latest AS (
    -- Estado ACTUAL por clave sobre TODO el histórico bronze. La entidad es
    -- incremental: quedarse con MAX(load_date) colapsaría la población al
    -- delta del día. Un borrado en la fuente no se refleja hasta una carga
    -- completa; ver README del cartucho.
    SELECT *
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
)
SELECT
    CAST(DocEntry AS BIGINT)                    AS doc_entry,
    CAST(DocNum AS BIGINT)                      AS doc_num,
    CAST(CANCELED AS VARCHAR)                   AS canceled,
    CAST(DocStatus AS VARCHAR)                  AS doc_status,
    CAST(ObjType AS VARCHAR)                    AS obj_type,
    CAST(DocDate AS TIMESTAMP)                  AS doc_date,
    CAST(TaxDate AS TIMESTAMP)                  AS tax_date,
    CAST(CardCode AS VARCHAR)                   AS card_code,
    CAST(CardName AS VARCHAR)                   AS card_name,
    CAST(Filler AS VARCHAR)                     AS filler,
    CAST(ToWhsCode AS VARCHAR)                  AS to_whs_code,
    CAST(Comments AS VARCHAR)                   AS comments,
    CAST(TransId AS BIGINT)                     AS trans_id,
    CAST(Series AS BIGINT)                      AS series,
    CAST(CreateDate AS TIMESTAMP)               AS create_date,
    CAST(CreateTS AS BIGINT)                    AS create_ts,
    CAST(UpdateDate AS TIMESTAMP)               AS update_date,
    CAST(UpdateTS AS BIGINT)                    AS update_ts,
    CAST(UserSign AS BIGINT)                    AS user_sign,
    _company                              AS company,
    _source_updated_at                    AS source_updated_at,
    load_date
FROM latest
ORDER BY company, doc_entry
$seed$, $seed$Inventory transfer headers, ObjType 67: source warehouse (Filler) and target warehouse (ToWhsCode); stock moves, money does not.$seed$, $seed${}$seed$::jsonb, $seed$$seed$, NOW(), :'workspace_id'::uuid),
($seed$sap_b1_pch1_latest$seed$, $seed$silver$seed$, $seed$sap_b1$seed$, $seed$["raw/sap_b1/PCH1"]$seed$::jsonb, $seed$
-- sap_b1_pch1_latest  (silver)  cartridge: sap_b1
-- sources: ["raw/sap_b1/PCH1"]
-- description: A/P invoices lines, read through OPCH: quantities, prices, line totals in three currencies, base/target links.

WITH versions AS (
    -- Estado ACTUAL por clave sobre TODO el histórico bronze; después solo
    -- las líneas que llevan la marca más reciente de su cabecera: una línea
    -- borrada del documento desaparece en cuanto la cabecera se relee.
    SELECT *
    FROM (
        SELECT *,
               ROW_NUMBER() OVER (
                   PARTITION BY _company, DocEntry, LineNum
                   ORDER BY _source_updated_at DESC NULLS LAST, load_date DESC, _extracted_at DESC
               ) AS _rn
        FROM read_parquet('s3://{bucket}/raw/sap_b1/PCH1/**/*.parquet',
                      hive_partitioning = true,
                      union_by_name   = true)
    )
    WHERE _rn = 1
),
current_lines AS (
    SELECT *
    FROM (
        SELECT *,
               MAX(_source_updated_at) OVER (PARTITION BY _company, DocEntry) AS _header_stamp
        FROM versions
    )
    WHERE _source_updated_at IS NULL OR _source_updated_at = _header_stamp
)
SELECT
    CAST(DocEntry AS BIGINT)                    AS doc_entry,
    CAST(LineNum AS BIGINT)                     AS line_num,
    CAST(TargetType AS BIGINT)                  AS target_type,
    CAST(TrgetEntry AS BIGINT)                  AS trget_entry,
    CAST(BaseType AS BIGINT)                    AS base_type,
    CAST(BaseEntry AS BIGINT)                   AS base_entry,
    CAST(BaseLine AS BIGINT)                    AS base_line,
    CAST(LineStatus AS VARCHAR)                 AS line_status,
    CAST(ItemCode AS VARCHAR)                   AS item_code,
    CAST(Dscription AS VARCHAR)                 AS dscription,
    CAST(Quantity AS DECIMAL(19,6))             AS quantity,
    CAST(OpenQty AS DECIMAL(19,6))              AS open_qty,
    CAST(Price AS DECIMAL(19,6))                AS price,
    CAST(PriceBefDi AS DECIMAL(19,6))           AS price_bef_di,
    CAST(Currency AS VARCHAR)                   AS currency,
    CAST(Rate AS DECIMAL(19,6))                 AS rate,
    CAST(DiscPrcnt AS DECIMAL(19,6))            AS disc_prcnt,
    CAST(LineTotal AS DECIMAL(19,6))            AS line_total,
    CAST(TotalFrgn AS DECIMAL(19,6))            AS total_frgn,
    CAST(TotalSumSy AS DECIMAL(19,6))           AS total_sum_sy,
    CAST(GrssProfit AS DECIMAL(19,6))           AS grss_profit,
    CAST(GrssProfFC AS DECIMAL(19,6))           AS grss_prof_fc,
    CAST(GrssProfSC AS DECIMAL(19,6))           AS grss_prof_sc,
    CAST(StockPrice AS DECIMAL(19,6))           AS stock_price,
    CAST(WhsCode AS VARCHAR)                    AS whs_code,
    CAST(ShipDate AS TIMESTAMP)                 AS ship_date,
    CAST(VatPrcnt AS DECIMAL(19,6))             AS vat_prcnt,
    CAST(VatSum AS DECIMAL(19,6))               AS vat_sum,
    CAST(AcctCode AS VARCHAR)                   AS acct_code,
    CAST(OcrCode AS VARCHAR)                    AS ocr_code,
    CAST(LineType AS VARCHAR)                   AS line_type,
    CAST(TreeType AS VARCHAR)                   AS tree_type,
    CAST(ObjType AS VARCHAR)                    AS obj_type,
    CAST(VisOrder AS BIGINT)                    AS vis_order,
    _company                              AS company,
    _source_updated_at                    AS source_updated_at,
    load_date
FROM current_lines
ORDER BY company, doc_entry, line_num
$seed$, $seed$A/P invoices lines, read through OPCH: quantities, prices, line totals in three currencies, base/target links.$seed$, $seed${}$seed$::jsonb, $seed$$seed$, NOW(), :'workspace_id'::uuid),
($seed$sap_b1_pdn1_latest$seed$, $seed$silver$seed$, $seed$sap_b1$seed$, $seed$["raw/sap_b1/PDN1"]$seed$::jsonb, $seed$
-- sap_b1_pdn1_latest  (silver)  cartridge: sap_b1
-- sources: ["raw/sap_b1/PDN1"]
-- description: Goods receipt POs lines, read through OPDN: quantities, prices, line totals in three currencies, base/target links.

WITH versions AS (
    -- Estado ACTUAL por clave sobre TODO el histórico bronze; después solo
    -- las líneas que llevan la marca más reciente de su cabecera: una línea
    -- borrada del documento desaparece en cuanto la cabecera se relee.
    SELECT *
    FROM (
        SELECT *,
               ROW_NUMBER() OVER (
                   PARTITION BY _company, DocEntry, LineNum
                   ORDER BY _source_updated_at DESC NULLS LAST, load_date DESC, _extracted_at DESC
               ) AS _rn
        FROM read_parquet('s3://{bucket}/raw/sap_b1/PDN1/**/*.parquet',
                      hive_partitioning = true,
                      union_by_name   = true)
    )
    WHERE _rn = 1
),
current_lines AS (
    SELECT *
    FROM (
        SELECT *,
               MAX(_source_updated_at) OVER (PARTITION BY _company, DocEntry) AS _header_stamp
        FROM versions
    )
    WHERE _source_updated_at IS NULL OR _source_updated_at = _header_stamp
)
SELECT
    CAST(DocEntry AS BIGINT)                    AS doc_entry,
    CAST(LineNum AS BIGINT)                     AS line_num,
    CAST(TargetType AS BIGINT)                  AS target_type,
    CAST(TrgetEntry AS BIGINT)                  AS trget_entry,
    CAST(BaseType AS BIGINT)                    AS base_type,
    CAST(BaseEntry AS BIGINT)                   AS base_entry,
    CAST(BaseLine AS BIGINT)                    AS base_line,
    CAST(LineStatus AS VARCHAR)                 AS line_status,
    CAST(ItemCode AS VARCHAR)                   AS item_code,
    CAST(Dscription AS VARCHAR)                 AS dscription,
    CAST(Quantity AS DECIMAL(19,6))             AS quantity,
    CAST(OpenQty AS DECIMAL(19,6))              AS open_qty,
    CAST(Price AS DECIMAL(19,6))                AS price,
    CAST(PriceBefDi AS DECIMAL(19,6))           AS price_bef_di,
    CAST(Currency AS VARCHAR)                   AS currency,
    CAST(Rate AS DECIMAL(19,6))                 AS rate,
    CAST(DiscPrcnt AS DECIMAL(19,6))            AS disc_prcnt,
    CAST(LineTotal AS DECIMAL(19,6))            AS line_total,
    CAST(TotalFrgn AS DECIMAL(19,6))            AS total_frgn,
    CAST(TotalSumSy AS DECIMAL(19,6))           AS total_sum_sy,
    CAST(GrssProfit AS DECIMAL(19,6))           AS grss_profit,
    CAST(GrssProfFC AS DECIMAL(19,6))           AS grss_prof_fc,
    CAST(GrssProfSC AS DECIMAL(19,6))           AS grss_prof_sc,
    CAST(StockPrice AS DECIMAL(19,6))           AS stock_price,
    CAST(WhsCode AS VARCHAR)                    AS whs_code,
    CAST(ShipDate AS TIMESTAMP)                 AS ship_date,
    CAST(VatPrcnt AS DECIMAL(19,6))             AS vat_prcnt,
    CAST(VatSum AS DECIMAL(19,6))               AS vat_sum,
    CAST(AcctCode AS VARCHAR)                   AS acct_code,
    CAST(OcrCode AS VARCHAR)                    AS ocr_code,
    CAST(LineType AS VARCHAR)                   AS line_type,
    CAST(TreeType AS VARCHAR)                   AS tree_type,
    CAST(ObjType AS VARCHAR)                    AS obj_type,
    CAST(VisOrder AS BIGINT)                    AS vis_order,
    _company                              AS company,
    _source_updated_at                    AS source_updated_at,
    load_date
FROM current_lines
ORDER BY company, doc_entry, line_num
$seed$, $seed$Goods receipt POs lines, read through OPDN: quantities, prices, line totals in three currencies, base/target links.$seed$, $seed${}$seed$::jsonb, $seed$$seed$, NOW(), :'workspace_id'::uuid),
($seed$sap_b1_pnl_by_company_month$seed$, $seed$gold$seed$, $seed$sap_b1$seed$, $seed$["raw/sap_b1/OJDT", "raw/sap_b1/JDT1", "raw/sap_b1/OACT", "raw/sap_b1/OADM"]$seed$::jsonb, $seed$
-- sap_b1_pnl_by_company_month  (gold)  cartridge: sap_b1
-- sources: ["raw/sap_b1/OJDT", "raw/sap_b1/JDT1", "raw/sap_b1/OACT", "raw/sap_b1/OADM"]
-- description: The accounting view per company and month from the journal: income accounts (type I) as revenue, expense accounts (type E) as expenses, and the operating result, in local and system currency. Reversals land in the month they were posted, so a month can differ from the document view; period totals agree.

WITH lines AS (
    SELECT company, doc_month, local_currency, sys_currency, account_type,
           net_debit_local, net_debit_sys
    FROM read_parquet('s3://{bucket}/silver/sap_b1/sap_b1_journal_lines/**/*.parquet')
    WHERE account_type IN ('I', 'E')
)
SELECT
    company,
    doc_month,
    local_currency,
    sys_currency,
    ROUND(SUM(CASE WHEN account_type = 'I' THEN -net_debit_local ELSE 0 END), 2)  AS revenue_local,
    ROUND(SUM(CASE WHEN account_type = 'E' THEN net_debit_local ELSE 0 END), 2)   AS expenses_local,
    ROUND(SUM(CASE WHEN account_type = 'I' THEN -net_debit_local ELSE net_debit_local * -1 END), 2) AS operating_result_local,
    ROUND(SUM(CASE WHEN account_type = 'I' THEN -net_debit_sys ELSE 0 END), 2)    AS revenue_sys,
    ROUND(SUM(CASE WHEN account_type = 'E' THEN net_debit_sys ELSE 0 END), 2)     AS expenses_sys,
    ROUND(SUM(CASE WHEN account_type = 'I' THEN -net_debit_sys ELSE net_debit_sys * -1 END), 2) AS operating_result_sys
FROM lines
GROUP BY 1, 2, 3, 4
ORDER BY company, doc_month
$seed$, $seed$The accounting view per company and month from the journal: income accounts (type I) as revenue, expense accounts (type E) as expenses, and the operating result, in local and system currency. Reversals land in the month they were posted, so a month can differ from the document view; period totals agree.$seed$, $seed${}$seed$::jsonb, $seed$$seed$, NOW(), :'workspace_id'::uuid),
($seed$sap_b1_por1_latest$seed$, $seed$silver$seed$, $seed$sap_b1$seed$, $seed$["raw/sap_b1/POR1"]$seed$::jsonb, $seed$
-- sap_b1_por1_latest  (silver)  cartridge: sap_b1
-- sources: ["raw/sap_b1/POR1"]
-- description: Purchase orders lines, read through OPOR: quantities, prices, line totals in three currencies, base/target links.

WITH versions AS (
    -- Estado ACTUAL por clave sobre TODO el histórico bronze; después solo
    -- las líneas que llevan la marca más reciente de su cabecera: una línea
    -- borrada del documento desaparece en cuanto la cabecera se relee.
    SELECT *
    FROM (
        SELECT *,
               ROW_NUMBER() OVER (
                   PARTITION BY _company, DocEntry, LineNum
                   ORDER BY _source_updated_at DESC NULLS LAST, load_date DESC, _extracted_at DESC
               ) AS _rn
        FROM read_parquet('s3://{bucket}/raw/sap_b1/POR1/**/*.parquet',
                      hive_partitioning = true,
                      union_by_name   = true)
    )
    WHERE _rn = 1
),
current_lines AS (
    SELECT *
    FROM (
        SELECT *,
               MAX(_source_updated_at) OVER (PARTITION BY _company, DocEntry) AS _header_stamp
        FROM versions
    )
    WHERE _source_updated_at IS NULL OR _source_updated_at = _header_stamp
)
SELECT
    CAST(DocEntry AS BIGINT)                    AS doc_entry,
    CAST(LineNum AS BIGINT)                     AS line_num,
    CAST(TargetType AS BIGINT)                  AS target_type,
    CAST(TrgetEntry AS BIGINT)                  AS trget_entry,
    CAST(BaseType AS BIGINT)                    AS base_type,
    CAST(BaseEntry AS BIGINT)                   AS base_entry,
    CAST(BaseLine AS BIGINT)                    AS base_line,
    CAST(LineStatus AS VARCHAR)                 AS line_status,
    CAST(ItemCode AS VARCHAR)                   AS item_code,
    CAST(Dscription AS VARCHAR)                 AS dscription,
    CAST(Quantity AS DECIMAL(19,6))             AS quantity,
    CAST(OpenQty AS DECIMAL(19,6))              AS open_qty,
    CAST(Price AS DECIMAL(19,6))                AS price,
    CAST(PriceBefDi AS DECIMAL(19,6))           AS price_bef_di,
    CAST(Currency AS VARCHAR)                   AS currency,
    CAST(Rate AS DECIMAL(19,6))                 AS rate,
    CAST(DiscPrcnt AS DECIMAL(19,6))            AS disc_prcnt,
    CAST(LineTotal AS DECIMAL(19,6))            AS line_total,
    CAST(TotalFrgn AS DECIMAL(19,6))            AS total_frgn,
    CAST(TotalSumSy AS DECIMAL(19,6))           AS total_sum_sy,
    CAST(GrssProfit AS DECIMAL(19,6))           AS grss_profit,
    CAST(GrssProfFC AS DECIMAL(19,6))           AS grss_prof_fc,
    CAST(GrssProfSC AS DECIMAL(19,6))           AS grss_prof_sc,
    CAST(StockPrice AS DECIMAL(19,6))           AS stock_price,
    CAST(WhsCode AS VARCHAR)                    AS whs_code,
    CAST(ShipDate AS TIMESTAMP)                 AS ship_date,
    CAST(VatPrcnt AS DECIMAL(19,6))             AS vat_prcnt,
    CAST(VatSum AS DECIMAL(19,6))               AS vat_sum,
    CAST(AcctCode AS VARCHAR)                   AS acct_code,
    CAST(OcrCode AS VARCHAR)                    AS ocr_code,
    CAST(LineType AS VARCHAR)                   AS line_type,
    CAST(TreeType AS VARCHAR)                   AS tree_type,
    CAST(ObjType AS VARCHAR)                    AS obj_type,
    CAST(VisOrder AS BIGINT)                    AS vis_order,
    _company                              AS company,
    _source_updated_at                    AS source_updated_at,
    load_date
FROM current_lines
ORDER BY company, doc_entry, line_num
$seed$, $seed$Purchase orders lines, read through OPOR: quantities, prices, line totals in three currencies, base/target links.$seed$, $seed${}$seed$::jsonb, $seed$$seed$, NOW(), :'workspace_id'::uuid),
($seed$sap_b1_production_orders$seed$, $seed$silver$seed$, $seed$sap_b1$seed$, $seed$["raw/sap_b1/OWOR", "raw/sap_b1/WOR1"]$seed$::jsonb, $seed$
-- sap_b1_production_orders  (silver)  cartridge: sap_b1
-- sources: ["raw/sap_b1/OWOR", "raw/sap_b1/WOR1"]
-- description: Production orders with their components (OWOR/WOR1): planned, completed and rejected quantities, dates and status; one row per component line.

WITH orders AS (
    SELECT * EXCLUDE (_rn)
    FROM (
        SELECT *,
               ROW_NUMBER() OVER (
                   PARTITION BY _company, DocEntry
                   ORDER BY _source_updated_at DESC NULLS LAST, load_date DESC, _extracted_at DESC
               ) AS _rn
        FROM read_parquet('s3://{bucket}/raw/sap_b1/OWOR/**/*.parquet',
                          hive_partitioning = true,
                          union_by_name   = true)
    )
    WHERE _rn = 1
),
component_versions AS (
    SELECT * EXCLUDE (_rn)
    FROM (
        SELECT *,
               ROW_NUMBER() OVER (
                   PARTITION BY _company, DocEntry, LineNum
                   ORDER BY _source_updated_at DESC NULLS LAST, load_date DESC, _extracted_at DESC
               ) AS _rn
        FROM read_parquet('s3://{bucket}/raw/sap_b1/WOR1/**/*.parquet',
                          hive_partitioning = true,
                          union_by_name   = true)
    )
    WHERE _rn = 1
),
components AS (
    SELECT * EXCLUDE (_order_stamp)
    FROM (
        SELECT *, MAX(_source_updated_at) OVER (PARTITION BY _company, DocEntry) AS _order_stamp
        FROM component_versions
    )
    WHERE _source_updated_at IS NULL OR _source_updated_at = _order_stamp
)
SELECT
    o._company                                          AS company,
    CAST(o.DocEntry AS BIGINT)                          AS doc_entry,
    CAST(o.DocNum AS BIGINT)                            AS doc_num,
    CAST(o.Status AS VARCHAR)                           AS status,
    CAST(o.Type AS VARCHAR)                             AS order_type,
    CAST(o.ItemCode AS VARCHAR)                         AS product_item_code,
    CAST(o.PlannedQty AS DECIMAL(19,6))                 AS planned_qty,
    CAST(o.CmpltQty AS DECIMAL(19,6))                   AS completed_qty,
    CAST(o.RjctQty AS DECIMAL(19,6))                    AS rejected_qty,
    CAST(o.PostDate AS TIMESTAMP)                       AS posting_date,
    CAST(DATE_TRUNC('month', CAST(o.PostDate AS TIMESTAMP)) AS DATE) AS doc_month,
    CAST(o.DueDate AS TIMESTAMP)                        AS due_date,
    CAST(o.StartDate AS TIMESTAMP)                      AS start_date,
    CAST(o.CloseDate AS TIMESTAMP)                      AS close_date,
    CAST(o.Warehouse AS VARCHAR)                        AS warehouse,
    CAST(c.LineNum AS BIGINT)                           AS component_line,
    CAST(c.ItemCode AS VARCHAR)                         AS component_item_code,
    CAST(c.BaseQty AS DECIMAL(19,6))                    AS component_base_qty,
    CAST(c.PlannedQty AS DECIMAL(19,6))                 AS component_planned_qty,
    CAST(c.IssuedQty AS DECIMAL(19,6))                  AS component_issued_qty,
    CAST(c.wareHouse AS VARCHAR)                        AS component_warehouse,
    o._source_updated_at                                AS source_updated_at,
    o.load_date
FROM orders o
LEFT JOIN components c ON c._company = o._company AND c.DocEntry = o.DocEntry
ORDER BY company, doc_entry, component_line
$seed$, $seed$Production orders with their components (OWOR/WOR1): planned, completed and rejected quantities, dates and status; one row per component line.$seed$, $seed${}$seed$::jsonb, $seed$$seed$, NOW(), :'workspace_id'::uuid),
($seed$sap_b1_purchase_order_lines$seed$, $seed$silver$seed$, $seed$sap_b1$seed$, $seed$["raw/sap_b1/OPOR", "raw/sap_b1/POR1", "raw/sap_b1/OADM", "raw/sap_b1/IntercompanyPartners"]$seed$::jsonb, $seed$
-- sap_b1_purchase_order_lines  (silver)  cartridge: sap_b1
-- sources: ["raw/sap_b1/OPOR", "raw/sap_b1/POR1", "raw/sap_b1/OADM", "raw/sap_b1/IntercompanyPartners"]
-- description: purchase order lines with their header (OPOR/POR1, ObjType 22): amounts in document, local and system currency, the cost the line carries, and the intercompany flag from the configured partner mapping. CANCELED is kept (N/Y/C); gold filters it.

WITH headers AS (
    -- Current version of every header per company over the whole bronze history.
    SELECT * EXCLUDE (_rn)
    FROM (
        SELECT *,
               ROW_NUMBER() OVER (
                   PARTITION BY _company, DocEntry
                   ORDER BY _source_updated_at DESC NULLS LAST, load_date DESC, _extracted_at DESC
               ) AS _rn
        FROM read_parquet('s3://{bucket}/raw/sap_b1/OPOR/**/*.parquet',
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
        FROM read_parquet('s3://{bucket}/raw/sap_b1/POR1/**/*.parquet',
                          hive_partitioning = true,
                          union_by_name   = true)
    )
    WHERE _rn = 1
),
lines AS (
    -- Only the lines that carry the header's latest stamp: a line dropped from
    -- the document disappears the moment the document is re-read.
    SELECT * EXCLUDE (_header_stamp)
    FROM (
        SELECT *,
               MAX(_source_updated_at) OVER (PARTITION BY _company, DocEntry) AS _header_stamp
        FROM line_versions
    )
    WHERE _source_updated_at IS NULL OR _source_updated_at = _header_stamp
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
),
partners AS (
    -- Newest run per company (all of its batches), never a mix of two runs.
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
    -- Currency, explicit on every row: the document's, the company's local
    -- and the company's system currency. DocRate is 0 on a local-currency
    -- document, as Business One stores it.
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
    -- The cost the line carries: Business One's stock price at posting time
    -- times the quantity, and its own gross profit figures.
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
$seed$, $seed$purchase order lines with their header (OPOR/POR1, ObjType 22): amounts in document, local and system currency, the cost the line carries, and the intercompany flag from the configured partner mapping. CANCELED is kept (N/Y/C); gold filters it.$seed$, $seed${}$seed$::jsonb, $seed$$seed$, NOW(), :'workspace_id'::uuid),
($seed$sap_b1_purchases_by_company_month$seed$, $seed$gold$seed$, $seed$sap_b1$seed$, $seed$["raw/sap_b1/OPCH", "raw/sap_b1/PCH1", "raw/sap_b1/ORPC", "raw/sap_b1/RPC1", "raw/sap_b1/OADM", "raw/sap_b1/IntercompanyPartners"]$seed$::jsonb, $seed$
-- sap_b1_purchases_by_company_month  (gold)  cartridge: sap_b1
-- sources: ["raw/sap_b1/OPCH", "raw/sap_b1/PCH1", "raw/sap_b1/ORPC", "raw/sap_b1/RPC1", "raw/sap_b1/OADM", "raw/sap_b1/IntercompanyPartners"]
-- description: Purchases per company and month from supplier invoices net of supplier credit memos, split between external suppliers and group companies, in local and system currency. Cancelled documents are excluded.

WITH invoices AS (
    SELECT company, doc_month, local_currency, sys_currency,
           CASE WHEN is_intercompany THEN 'intercompany' ELSE 'external' END AS scope,
           doc_entry, card_code, amount_local, amount_sys
    FROM read_parquet('s3://{bucket}/silver/sap_b1/sap_b1_ap_invoice_lines/**/*.parquet')
    WHERE canceled = 'N'
),
credits AS (
    SELECT company, doc_month,
           CASE WHEN is_intercompany THEN 'intercompany' ELSE 'external' END AS scope,
           doc_entry, amount_local, amount_sys
    FROM read_parquet('s3://{bucket}/silver/sap_b1/sap_b1_ap_credit_memo_lines/**/*.parquet')
    WHERE canceled = 'N'
),
invoice_totals AS (
    SELECT company, doc_month, local_currency, sys_currency, scope,
           COUNT(DISTINCT doc_entry) AS supplier_invoices,
           COUNT(DISTINCT card_code) AS suppliers,
           SUM(amount_local)         AS purchases_gross_local,
           SUM(amount_sys)           AS purchases_gross_sys
    FROM invoices
    GROUP BY 1, 2, 3, 4, 5
),
credit_totals AS (
    SELECT company, doc_month, scope,
           COUNT(DISTINCT doc_entry) AS credit_memos,
           SUM(amount_local)         AS credit_local,
           SUM(amount_sys)           AS credit_sys
    FROM credits
    GROUP BY 1, 2, 3
)
SELECT
    i.company,
    i.doc_month,
    i.scope,
    i.local_currency,
    i.sys_currency,
    i.supplier_invoices,
    i.suppliers,
    COALESCE(c.credit_memos, 0)                                     AS credit_memos,
    ROUND(i.purchases_gross_local, 2)                               AS purchases_gross_local,
    ROUND(COALESCE(c.credit_local, 0), 2)                           AS credit_memos_local,
    ROUND(i.purchases_gross_local - COALESCE(c.credit_local, 0), 2) AS purchases_net_local,
    ROUND(i.purchases_gross_sys - COALESCE(c.credit_sys, 0), 2)     AS purchases_net_sys
FROM invoice_totals i
LEFT JOIN credit_totals c
  ON c.company = i.company AND c.doc_month = i.doc_month AND c.scope = i.scope
ORDER BY i.company, i.doc_month, i.scope
$seed$, $seed$Purchases per company and month from supplier invoices net of supplier credit memos, split between external suppliers and group companies, in local and system currency. Cancelled documents are excluded.$seed$, $seed${}$seed$::jsonb, $seed$$seed$, NOW(), :'workspace_id'::uuid),
($seed$sap_b1_rdn1_latest$seed$, $seed$silver$seed$, $seed$sap_b1$seed$, $seed$["raw/sap_b1/RDN1"]$seed$::jsonb, $seed$
-- sap_b1_rdn1_latest  (silver)  cartridge: sap_b1
-- sources: ["raw/sap_b1/RDN1"]
-- description: Returns lines, read through ORDN: quantities, prices, line totals in three currencies, base/target links.

WITH versions AS (
    -- Estado ACTUAL por clave sobre TODO el histórico bronze; después solo
    -- las líneas que llevan la marca más reciente de su cabecera: una línea
    -- borrada del documento desaparece en cuanto la cabecera se relee.
    SELECT *
    FROM (
        SELECT *,
               ROW_NUMBER() OVER (
                   PARTITION BY _company, DocEntry, LineNum
                   ORDER BY _source_updated_at DESC NULLS LAST, load_date DESC, _extracted_at DESC
               ) AS _rn
        FROM read_parquet('s3://{bucket}/raw/sap_b1/RDN1/**/*.parquet',
                      hive_partitioning = true,
                      union_by_name   = true)
    )
    WHERE _rn = 1
),
current_lines AS (
    SELECT *
    FROM (
        SELECT *,
               MAX(_source_updated_at) OVER (PARTITION BY _company, DocEntry) AS _header_stamp
        FROM versions
    )
    WHERE _source_updated_at IS NULL OR _source_updated_at = _header_stamp
)
SELECT
    CAST(DocEntry AS BIGINT)                    AS doc_entry,
    CAST(LineNum AS BIGINT)                     AS line_num,
    CAST(TargetType AS BIGINT)                  AS target_type,
    CAST(TrgetEntry AS BIGINT)                  AS trget_entry,
    CAST(BaseType AS BIGINT)                    AS base_type,
    CAST(BaseEntry AS BIGINT)                   AS base_entry,
    CAST(BaseLine AS BIGINT)                    AS base_line,
    CAST(LineStatus AS VARCHAR)                 AS line_status,
    CAST(ItemCode AS VARCHAR)                   AS item_code,
    CAST(Dscription AS VARCHAR)                 AS dscription,
    CAST(Quantity AS DECIMAL(19,6))             AS quantity,
    CAST(OpenQty AS DECIMAL(19,6))              AS open_qty,
    CAST(Price AS DECIMAL(19,6))                AS price,
    CAST(PriceBefDi AS DECIMAL(19,6))           AS price_bef_di,
    CAST(Currency AS VARCHAR)                   AS currency,
    CAST(Rate AS DECIMAL(19,6))                 AS rate,
    CAST(DiscPrcnt AS DECIMAL(19,6))            AS disc_prcnt,
    CAST(LineTotal AS DECIMAL(19,6))            AS line_total,
    CAST(TotalFrgn AS DECIMAL(19,6))            AS total_frgn,
    CAST(TotalSumSy AS DECIMAL(19,6))           AS total_sum_sy,
    CAST(GrssProfit AS DECIMAL(19,6))           AS grss_profit,
    CAST(GrssProfFC AS DECIMAL(19,6))           AS grss_prof_fc,
    CAST(GrssProfSC AS DECIMAL(19,6))           AS grss_prof_sc,
    CAST(StockPrice AS DECIMAL(19,6))           AS stock_price,
    CAST(WhsCode AS VARCHAR)                    AS whs_code,
    CAST(ShipDate AS TIMESTAMP)                 AS ship_date,
    CAST(VatPrcnt AS DECIMAL(19,6))             AS vat_prcnt,
    CAST(VatSum AS DECIMAL(19,6))               AS vat_sum,
    CAST(AcctCode AS VARCHAR)                   AS acct_code,
    CAST(OcrCode AS VARCHAR)                    AS ocr_code,
    CAST(LineType AS VARCHAR)                   AS line_type,
    CAST(TreeType AS VARCHAR)                   AS tree_type,
    CAST(ObjType AS VARCHAR)                    AS obj_type,
    CAST(VisOrder AS BIGINT)                    AS vis_order,
    _company                              AS company,
    _source_updated_at                    AS source_updated_at,
    load_date
FROM current_lines
ORDER BY company, doc_entry, line_num
$seed$, $seed$Returns lines, read through ORDN: quantities, prices, line totals in three currencies, base/target links.$seed$, $seed${}$seed$::jsonb, $seed$$seed$, NOW(), :'workspace_id'::uuid),
($seed$sap_b1_rdr1_latest$seed$, $seed$silver$seed$, $seed$sap_b1$seed$, $seed$["raw/sap_b1/RDR1"]$seed$::jsonb, $seed$
-- sap_b1_rdr1_latest  (silver)  cartridge: sap_b1
-- sources: ["raw/sap_b1/RDR1"]
-- description: Sales orders lines, read through ORDR: quantities, prices, line totals in three currencies, base/target links.

WITH versions AS (
    -- Estado ACTUAL por clave sobre TODO el histórico bronze; después solo
    -- las líneas que llevan la marca más reciente de su cabecera: una línea
    -- borrada del documento desaparece en cuanto la cabecera se relee.
    SELECT *
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
current_lines AS (
    SELECT *
    FROM (
        SELECT *,
               MAX(_source_updated_at) OVER (PARTITION BY _company, DocEntry) AS _header_stamp
        FROM versions
    )
    WHERE _source_updated_at IS NULL OR _source_updated_at = _header_stamp
)
SELECT
    CAST(DocEntry AS BIGINT)                    AS doc_entry,
    CAST(LineNum AS BIGINT)                     AS line_num,
    CAST(TargetType AS BIGINT)                  AS target_type,
    CAST(TrgetEntry AS BIGINT)                  AS trget_entry,
    CAST(BaseType AS BIGINT)                    AS base_type,
    CAST(BaseEntry AS BIGINT)                   AS base_entry,
    CAST(BaseLine AS BIGINT)                    AS base_line,
    CAST(LineStatus AS VARCHAR)                 AS line_status,
    CAST(ItemCode AS VARCHAR)                   AS item_code,
    CAST(Dscription AS VARCHAR)                 AS dscription,
    CAST(Quantity AS DECIMAL(19,6))             AS quantity,
    CAST(OpenQty AS DECIMAL(19,6))              AS open_qty,
    CAST(Price AS DECIMAL(19,6))                AS price,
    CAST(PriceBefDi AS DECIMAL(19,6))           AS price_bef_di,
    CAST(Currency AS VARCHAR)                   AS currency,
    CAST(Rate AS DECIMAL(19,6))                 AS rate,
    CAST(DiscPrcnt AS DECIMAL(19,6))            AS disc_prcnt,
    CAST(LineTotal AS DECIMAL(19,6))            AS line_total,
    CAST(TotalFrgn AS DECIMAL(19,6))            AS total_frgn,
    CAST(TotalSumSy AS DECIMAL(19,6))           AS total_sum_sy,
    CAST(GrssProfit AS DECIMAL(19,6))           AS grss_profit,
    CAST(GrssProfFC AS DECIMAL(19,6))           AS grss_prof_fc,
    CAST(GrssProfSC AS DECIMAL(19,6))           AS grss_prof_sc,
    CAST(StockPrice AS DECIMAL(19,6))           AS stock_price,
    CAST(WhsCode AS VARCHAR)                    AS whs_code,
    CAST(ShipDate AS TIMESTAMP)                 AS ship_date,
    CAST(VatPrcnt AS DECIMAL(19,6))             AS vat_prcnt,
    CAST(VatSum AS DECIMAL(19,6))               AS vat_sum,
    CAST(AcctCode AS VARCHAR)                   AS acct_code,
    CAST(OcrCode AS VARCHAR)                    AS ocr_code,
    CAST(LineType AS VARCHAR)                   AS line_type,
    CAST(TreeType AS VARCHAR)                   AS tree_type,
    CAST(ObjType AS VARCHAR)                    AS obj_type,
    CAST(VisOrder AS BIGINT)                    AS vis_order,
    _company                              AS company,
    _source_updated_at                    AS source_updated_at,
    load_date
FROM current_lines
ORDER BY company, doc_entry, line_num
$seed$, $seed$Sales orders lines, read through ORDR: quantities, prices, line totals in three currencies, base/target links.$seed$, $seed${}$seed$::jsonb, $seed$$seed$, NOW(), :'workspace_id'::uuid),
($seed$sap_b1_return_lines$seed$, $seed$silver$seed$, $seed$sap_b1$seed$, $seed$["raw/sap_b1/ORDN", "raw/sap_b1/RDN1", "raw/sap_b1/OADM", "raw/sap_b1/IntercompanyPartners"]$seed$::jsonb, $seed$
-- sap_b1_return_lines  (silver)  cartridge: sap_b1
-- sources: ["raw/sap_b1/ORDN", "raw/sap_b1/RDN1", "raw/sap_b1/OADM", "raw/sap_b1/IntercompanyPartners"]
-- description: return lines with their header (ORDN/RDN1, ObjType 16): amounts in document, local and system currency, the cost the line carries, and the intercompany flag from the configured partner mapping. CANCELED is kept (N/Y/C); gold filters it.

WITH headers AS (
    -- Current version of every header per company over the whole bronze history.
    SELECT * EXCLUDE (_rn)
    FROM (
        SELECT *,
               ROW_NUMBER() OVER (
                   PARTITION BY _company, DocEntry
                   ORDER BY _source_updated_at DESC NULLS LAST, load_date DESC, _extracted_at DESC
               ) AS _rn
        FROM read_parquet('s3://{bucket}/raw/sap_b1/ORDN/**/*.parquet',
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
        FROM read_parquet('s3://{bucket}/raw/sap_b1/RDN1/**/*.parquet',
                          hive_partitioning = true,
                          union_by_name   = true)
    )
    WHERE _rn = 1
),
lines AS (
    -- Only the lines that carry the header's latest stamp: a line dropped from
    -- the document disappears the moment the document is re-read.
    SELECT * EXCLUDE (_header_stamp)
    FROM (
        SELECT *,
               MAX(_source_updated_at) OVER (PARTITION BY _company, DocEntry) AS _header_stamp
        FROM line_versions
    )
    WHERE _source_updated_at IS NULL OR _source_updated_at = _header_stamp
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
),
partners AS (
    -- Newest run per company (all of its batches), never a mix of two runs.
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
    -- Currency, explicit on every row: the document's, the company's local
    -- and the company's system currency. DocRate is 0 on a local-currency
    -- document, as Business One stores it.
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
    -- The cost the line carries: Business One's stock price at posting time
    -- times the quantity, and its own gross profit figures.
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
$seed$, $seed$return lines with their header (ORDN/RDN1, ObjType 16): amounts in document, local and system currency, the cost the line carries, and the intercompany flag from the configured partner mapping. CANCELED is kept (N/Y/C); gold filters it.$seed$, $seed${}$seed$::jsonb, $seed$$seed$, NOW(), :'workspace_id'::uuid),
($seed$sap_b1_rin1_latest$seed$, $seed$silver$seed$, $seed$sap_b1$seed$, $seed$["raw/sap_b1/RIN1"]$seed$::jsonb, $seed$
-- sap_b1_rin1_latest  (silver)  cartridge: sap_b1
-- sources: ["raw/sap_b1/RIN1"]
-- description: A/R credit memos lines, read through ORIN: quantities, prices, line totals in three currencies, base/target links.

WITH versions AS (
    -- Estado ACTUAL por clave sobre TODO el histórico bronze; después solo
    -- las líneas que llevan la marca más reciente de su cabecera: una línea
    -- borrada del documento desaparece en cuanto la cabecera se relee.
    SELECT *
    FROM (
        SELECT *,
               ROW_NUMBER() OVER (
                   PARTITION BY _company, DocEntry, LineNum
                   ORDER BY _source_updated_at DESC NULLS LAST, load_date DESC, _extracted_at DESC
               ) AS _rn
        FROM read_parquet('s3://{bucket}/raw/sap_b1/RIN1/**/*.parquet',
                      hive_partitioning = true,
                      union_by_name   = true)
    )
    WHERE _rn = 1
),
current_lines AS (
    SELECT *
    FROM (
        SELECT *,
               MAX(_source_updated_at) OVER (PARTITION BY _company, DocEntry) AS _header_stamp
        FROM versions
    )
    WHERE _source_updated_at IS NULL OR _source_updated_at = _header_stamp
)
SELECT
    CAST(DocEntry AS BIGINT)                    AS doc_entry,
    CAST(LineNum AS BIGINT)                     AS line_num,
    CAST(TargetType AS BIGINT)                  AS target_type,
    CAST(TrgetEntry AS BIGINT)                  AS trget_entry,
    CAST(BaseType AS BIGINT)                    AS base_type,
    CAST(BaseEntry AS BIGINT)                   AS base_entry,
    CAST(BaseLine AS BIGINT)                    AS base_line,
    CAST(LineStatus AS VARCHAR)                 AS line_status,
    CAST(ItemCode AS VARCHAR)                   AS item_code,
    CAST(Dscription AS VARCHAR)                 AS dscription,
    CAST(Quantity AS DECIMAL(19,6))             AS quantity,
    CAST(OpenQty AS DECIMAL(19,6))              AS open_qty,
    CAST(Price AS DECIMAL(19,6))                AS price,
    CAST(PriceBefDi AS DECIMAL(19,6))           AS price_bef_di,
    CAST(Currency AS VARCHAR)                   AS currency,
    CAST(Rate AS DECIMAL(19,6))                 AS rate,
    CAST(DiscPrcnt AS DECIMAL(19,6))            AS disc_prcnt,
    CAST(LineTotal AS DECIMAL(19,6))            AS line_total,
    CAST(TotalFrgn AS DECIMAL(19,6))            AS total_frgn,
    CAST(TotalSumSy AS DECIMAL(19,6))           AS total_sum_sy,
    CAST(GrssProfit AS DECIMAL(19,6))           AS grss_profit,
    CAST(GrssProfFC AS DECIMAL(19,6))           AS grss_prof_fc,
    CAST(GrssProfSC AS DECIMAL(19,6))           AS grss_prof_sc,
    CAST(StockPrice AS DECIMAL(19,6))           AS stock_price,
    CAST(WhsCode AS VARCHAR)                    AS whs_code,
    CAST(ShipDate AS TIMESTAMP)                 AS ship_date,
    CAST(VatPrcnt AS DECIMAL(19,6))             AS vat_prcnt,
    CAST(VatSum AS DECIMAL(19,6))               AS vat_sum,
    CAST(AcctCode AS VARCHAR)                   AS acct_code,
    CAST(OcrCode AS VARCHAR)                    AS ocr_code,
    CAST(LineType AS VARCHAR)                   AS line_type,
    CAST(TreeType AS VARCHAR)                   AS tree_type,
    CAST(ObjType AS VARCHAR)                    AS obj_type,
    CAST(VisOrder AS BIGINT)                    AS vis_order,
    _company                              AS company,
    _source_updated_at                    AS source_updated_at,
    load_date
FROM current_lines
ORDER BY company, doc_entry, line_num
$seed$, $seed$A/R credit memos lines, read through ORIN: quantities, prices, line totals in three currencies, base/target links.$seed$, $seed${}$seed$::jsonb, $seed$$seed$, NOW(), :'workspace_id'::uuid),
($seed$sap_b1_rpc1_latest$seed$, $seed$silver$seed$, $seed$sap_b1$seed$, $seed$["raw/sap_b1/RPC1"]$seed$::jsonb, $seed$
-- sap_b1_rpc1_latest  (silver)  cartridge: sap_b1
-- sources: ["raw/sap_b1/RPC1"]
-- description: A/P credit memos lines, read through ORPC: quantities, prices, line totals in three currencies, base/target links.

WITH versions AS (
    -- Estado ACTUAL por clave sobre TODO el histórico bronze; después solo
    -- las líneas que llevan la marca más reciente de su cabecera: una línea
    -- borrada del documento desaparece en cuanto la cabecera se relee.
    SELECT *
    FROM (
        SELECT *,
               ROW_NUMBER() OVER (
                   PARTITION BY _company, DocEntry, LineNum
                   ORDER BY _source_updated_at DESC NULLS LAST, load_date DESC, _extracted_at DESC
               ) AS _rn
        FROM read_parquet('s3://{bucket}/raw/sap_b1/RPC1/**/*.parquet',
                      hive_partitioning = true,
                      union_by_name   = true)
    )
    WHERE _rn = 1
),
current_lines AS (
    SELECT *
    FROM (
        SELECT *,
               MAX(_source_updated_at) OVER (PARTITION BY _company, DocEntry) AS _header_stamp
        FROM versions
    )
    WHERE _source_updated_at IS NULL OR _source_updated_at = _header_stamp
)
SELECT
    CAST(DocEntry AS BIGINT)                    AS doc_entry,
    CAST(LineNum AS BIGINT)                     AS line_num,
    CAST(TargetType AS BIGINT)                  AS target_type,
    CAST(TrgetEntry AS BIGINT)                  AS trget_entry,
    CAST(BaseType AS BIGINT)                    AS base_type,
    CAST(BaseEntry AS BIGINT)                   AS base_entry,
    CAST(BaseLine AS BIGINT)                    AS base_line,
    CAST(LineStatus AS VARCHAR)                 AS line_status,
    CAST(ItemCode AS VARCHAR)                   AS item_code,
    CAST(Dscription AS VARCHAR)                 AS dscription,
    CAST(Quantity AS DECIMAL(19,6))             AS quantity,
    CAST(OpenQty AS DECIMAL(19,6))              AS open_qty,
    CAST(Price AS DECIMAL(19,6))                AS price,
    CAST(PriceBefDi AS DECIMAL(19,6))           AS price_bef_di,
    CAST(Currency AS VARCHAR)                   AS currency,
    CAST(Rate AS DECIMAL(19,6))                 AS rate,
    CAST(DiscPrcnt AS DECIMAL(19,6))            AS disc_prcnt,
    CAST(LineTotal AS DECIMAL(19,6))            AS line_total,
    CAST(TotalFrgn AS DECIMAL(19,6))            AS total_frgn,
    CAST(TotalSumSy AS DECIMAL(19,6))           AS total_sum_sy,
    CAST(GrssProfit AS DECIMAL(19,6))           AS grss_profit,
    CAST(GrssProfFC AS DECIMAL(19,6))           AS grss_prof_fc,
    CAST(GrssProfSC AS DECIMAL(19,6))           AS grss_prof_sc,
    CAST(StockPrice AS DECIMAL(19,6))           AS stock_price,
    CAST(WhsCode AS VARCHAR)                    AS whs_code,
    CAST(ShipDate AS TIMESTAMP)                 AS ship_date,
    CAST(VatPrcnt AS DECIMAL(19,6))             AS vat_prcnt,
    CAST(VatSum AS DECIMAL(19,6))               AS vat_sum,
    CAST(AcctCode AS VARCHAR)                   AS acct_code,
    CAST(OcrCode AS VARCHAR)                    AS ocr_code,
    CAST(LineType AS VARCHAR)                   AS line_type,
    CAST(TreeType AS VARCHAR)                   AS tree_type,
    CAST(ObjType AS VARCHAR)                    AS obj_type,
    CAST(VisOrder AS BIGINT)                    AS vis_order,
    _company                              AS company,
    _source_updated_at                    AS source_updated_at,
    load_date
FROM current_lines
ORDER BY company, doc_entry, line_num
$seed$, $seed$A/P credit memos lines, read through ORPC: quantities, prices, line totals in three currencies, base/target links.$seed$, $seed${}$seed$::jsonb, $seed$$seed$, NOW(), :'workspace_id'::uuid),
($seed$sap_b1_sales_by_company_month$seed$, $seed$gold$seed$, $seed$sap_b1$seed$, $seed$["raw/sap_b1/OINV", "raw/sap_b1/INV1", "raw/sap_b1/ORIN", "raw/sap_b1/RIN1", "raw/sap_b1/OADM", "raw/sap_b1/IntercompanyPartners"]$seed$::jsonb, $seed$
-- sap_b1_sales_by_company_month  (gold)  cartridge: sap_b1
-- sources: ["raw/sap_b1/OINV", "raw/sap_b1/INV1", "raw/sap_b1/ORIN", "raw/sap_b1/RIN1", "raw/sap_b1/OADM", "raw/sap_b1/IntercompanyPartners"]
-- description: Sales per company and month, split between external customers and group companies: invoices, credit memos, net revenue in local and system currency, the cost carried by the invoice lines and the resulting gross margin. Cancelled documents are excluded.

WITH invoices AS (
    SELECT company, doc_month, local_currency, sys_currency,
           CASE WHEN is_intercompany THEN 'intercompany' ELSE 'external' END AS scope,
           doc_entry, amount_local, amount_sys, cost_local, gross_profit_local, gross_profit_sys
    FROM read_parquet('s3://{bucket}/silver/sap_b1/sap_b1_ar_invoice_lines/**/*.parquet')
    WHERE canceled = 'N'
),
credits AS (
    SELECT company, doc_month,
           CASE WHEN is_intercompany THEN 'intercompany' ELSE 'external' END AS scope,
           doc_entry, amount_local, amount_sys
    FROM read_parquet('s3://{bucket}/silver/sap_b1/sap_b1_ar_credit_memo_lines/**/*.parquet')
    WHERE canceled = 'N'
),
invoice_totals AS (
    SELECT company, doc_month, local_currency, sys_currency, scope,
           COUNT(DISTINCT doc_entry)            AS invoices,
           SUM(amount_local)                    AS revenue_gross_local,
           SUM(amount_sys)                      AS revenue_gross_sys,
           SUM(cost_local)                      AS cost_local,
           SUM(gross_profit_local)              AS gross_profit_local,
           SUM(gross_profit_sys)                AS gross_profit_sys
    FROM invoices
    GROUP BY 1, 2, 3, 4, 5
),
credit_totals AS (
    SELECT company, doc_month, scope,
           COUNT(DISTINCT doc_entry)            AS credit_memos,
           SUM(amount_local)                    AS credit_local,
           SUM(amount_sys)                      AS credit_sys
    FROM credits
    GROUP BY 1, 2, 3
)
SELECT
    i.company,
    i.doc_month,
    i.scope,
    i.local_currency,
    i.sys_currency,
    i.invoices,
    COALESCE(c.credit_memos, 0)                                     AS credit_memos,
    ROUND(i.revenue_gross_local, 2)                                 AS revenue_gross_local,
    ROUND(COALESCE(c.credit_local, 0), 2)                           AS credit_memos_local,
    ROUND(i.revenue_gross_local - COALESCE(c.credit_local, 0), 2)   AS revenue_net_local,
    ROUND(i.revenue_gross_sys - COALESCE(c.credit_sys, 0), 2)       AS revenue_net_sys,
    ROUND(i.cost_local, 2)                                          AS cost_local,
    ROUND(i.gross_profit_local, 2)                                  AS gross_profit_local,
    ROUND(i.gross_profit_sys, 2)                                    AS gross_profit_sys,
    CASE WHEN i.revenue_gross_local <> 0
         THEN ROUND(100.0 * i.gross_profit_local / i.revenue_gross_local, 2) END AS gross_margin_pct
FROM invoice_totals i
LEFT JOIN credit_totals c
  ON c.company = i.company AND c.doc_month = i.doc_month AND c.scope = i.scope
ORDER BY i.company, i.doc_month, i.scope
$seed$, $seed$Sales per company and month, split between external customers and group companies: invoices, credit memos, net revenue in local and system currency, the cost carried by the invoice lines and the resulting gross margin. Cancelled documents are excluded.$seed$, $seed${}$seed$::jsonb, $seed$$seed$, NOW(), :'workspace_id'::uuid),
($seed$sap_b1_sales_consolidated_month$seed$, $seed$gold$seed$, $seed$sap_b1$seed$, $seed$["raw/sap_b1/OINV", "raw/sap_b1/INV1", "raw/sap_b1/ORIN", "raw/sap_b1/RIN1", "raw/sap_b1/OADM", "raw/sap_b1/IntercompanyPartners"]$seed$::jsonb, $seed$
-- sap_b1_sales_consolidated_month  (gold)  cartridge: sap_b1
-- sources: ["raw/sap_b1/OINV", "raw/sap_b1/INV1", "raw/sap_b1/ORIN", "raw/sap_b1/RIN1", "raw/sap_b1/OADM", "raw/sap_b1/IntercompanyPartners"]
-- description: Group sales per month with intercompany sales eliminated: only invoices to external customers count, per local currency, with the eliminated intercompany amount shown next to it. A month where a company's currency differs from the others is reported on its own row, never mixed.

WITH lines AS (
    SELECT company, doc_month, local_currency, is_intercompany, doc_entry,
           amount_local, cost_local, gross_profit_local
    FROM read_parquet('s3://{bucket}/silver/sap_b1/sap_b1_ar_invoice_lines/**/*.parquet')
    WHERE canceled = 'N'
),
credits AS (
    SELECT company, doc_month, local_currency, is_intercompany, amount_local
    FROM read_parquet('s3://{bucket}/silver/sap_b1/sap_b1_ar_credit_memo_lines/**/*.parquet')
    WHERE canceled = 'N'
),
by_month AS (
    SELECT doc_month, local_currency,
           COUNT(DISTINCT company)                                           AS companies,
           COUNT(DISTINCT CASE WHEN NOT is_intercompany THEN company || ':' || doc_entry END) AS external_invoices,
           SUM(CASE WHEN NOT is_intercompany THEN amount_local ELSE 0 END)   AS external_revenue_gross_local,
           SUM(CASE WHEN is_intercompany THEN amount_local ELSE 0 END)       AS intercompany_eliminated_local,
           SUM(CASE WHEN NOT is_intercompany THEN cost_local ELSE 0 END)     AS external_cost_local,
           SUM(CASE WHEN NOT is_intercompany THEN gross_profit_local ELSE 0 END) AS external_gross_profit_local
    FROM lines
    GROUP BY 1, 2
),
credit_month AS (
    SELECT doc_month, local_currency,
           SUM(CASE WHEN NOT is_intercompany THEN amount_local ELSE 0 END)   AS external_credit_local,
           SUM(CASE WHEN is_intercompany THEN amount_local ELSE 0 END)       AS intercompany_credit_eliminated_local
    FROM credits
    GROUP BY 1, 2
)
SELECT
    m.doc_month,
    m.local_currency,
    m.companies,
    m.external_invoices,
    ROUND(m.external_revenue_gross_local, 2)                                    AS revenue_gross_local,
    ROUND(COALESCE(c.external_credit_local, 0), 2)                              AS credit_memos_local,
    ROUND(m.external_revenue_gross_local - COALESCE(c.external_credit_local, 0), 2) AS revenue_net_local,
    ROUND(m.intercompany_eliminated_local - COALESCE(c.intercompany_credit_eliminated_local, 0), 2) AS intercompany_eliminated_local,
    ROUND(m.external_cost_local, 2)                                             AS cost_local,
    ROUND(m.external_gross_profit_local, 2)                                     AS gross_profit_local,
    CASE WHEN m.external_revenue_gross_local <> 0
         THEN ROUND(100.0 * m.external_gross_profit_local / m.external_revenue_gross_local, 2) END AS gross_margin_pct
FROM by_month m
LEFT JOIN credit_month c ON c.doc_month = m.doc_month AND c.local_currency = m.local_currency
ORDER BY m.doc_month, m.local_currency
$seed$, $seed$Group sales per month with intercompany sales eliminated: only invoices to external customers count, per local currency, with the eliminated intercompany amount shown next to it. A month where a company's currency differs from the others is reported on its own row, never mixed.$seed$, $seed${}$seed$::jsonb, $seed$$seed$, NOW(), :'workspace_id'::uuid),
($seed$sap_b1_sales_order_lines$seed$, $seed$silver$seed$, $seed$sap_b1$seed$, $seed$["raw/sap_b1/ORDR", "raw/sap_b1/RDR1", "raw/sap_b1/OADM", "raw/sap_b1/IntercompanyPartners"]$seed$::jsonb, $seed$
-- sap_b1_sales_order_lines  (silver)  cartridge: sap_b1
-- sources: ["raw/sap_b1/ORDR", "raw/sap_b1/RDR1", "raw/sap_b1/OADM", "raw/sap_b1/IntercompanyPartners"]
-- description: sales order lines with their header (ORDR/RDR1, ObjType 17): amounts in document, local and system currency, the cost the line carries, and the intercompany flag from the configured partner mapping. CANCELED is kept (N/Y/C); gold filters it.

WITH headers AS (
    -- Current version of every header per company over the whole bronze history.
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
    -- Only the lines that carry the header's latest stamp: a line dropped from
    -- the document disappears the moment the document is re-read.
    SELECT * EXCLUDE (_header_stamp)
    FROM (
        SELECT *,
               MAX(_source_updated_at) OVER (PARTITION BY _company, DocEntry) AS _header_stamp
        FROM line_versions
    )
    WHERE _source_updated_at IS NULL OR _source_updated_at = _header_stamp
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
),
partners AS (
    -- Newest run per company (all of its batches), never a mix of two runs.
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
    -- Currency, explicit on every row: the document's, the company's local
    -- and the company's system currency. DocRate is 0 on a local-currency
    -- document, as Business One stores it.
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
    -- The cost the line carries: Business One's stock price at posting time
    -- times the quantity, and its own gross profit figures.
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
$seed$, $seed$sales order lines with their header (ORDR/RDR1, ObjType 17): amounts in document, local and system currency, the cost the line carries, and the intercompany flag from the configured partner mapping. CANCELED is kept (N/Y/C); gold filters it.$seed$, $seed${}$seed$::jsonb, $seed$$seed$, NOW(), :'workspace_id'::uuid),
($seed$sap_b1_stock_by_company_warehouse$seed$, $seed$gold$seed$, $seed$sap_b1$seed$, $seed$["raw/sap_b1/OITW", "raw/sap_b1/OITM", "raw/sap_b1/OWHS", "raw/sap_b1/OADM"]$seed$::jsonb, $seed$
-- sap_b1_stock_by_company_warehouse  (gold)  cartridge: sap_b1
-- sources: ["raw/sap_b1/OITW", "raw/sap_b1/OITM", "raw/sap_b1/OWHS", "raw/sap_b1/OADM"]
-- description: Stock position per company and warehouse from the newest snapshot: items with stock, quantities on hand, committed and on order, and the stock value at average price in local currency.

SELECT
    company,
    warehouse,
    warehouse_name,
    local_currency,
    COUNT(*)                                            AS item_warehouse_rows,
    COUNT(CASE WHEN on_hand > 0 THEN 1 END)             AS items_with_stock,
    ROUND(SUM(on_hand), 6)                              AS on_hand_qty,
    ROUND(SUM(COALESCE(committed, 0)), 6)               AS committed_qty,
    ROUND(SUM(COALESCE(on_order, 0)), 6)                AS on_order_qty,
    ROUND(SUM(stock_value_local), 2)                    AS stock_value_local,
    MAX(load_date)                                      AS snapshot_load_date
FROM read_parquet('s3://{bucket}/silver/sap_b1/sap_b1_stock_on_hand/**/*.parquet')
GROUP BY 1, 2, 3, 4
ORDER BY company, warehouse
$seed$, $seed$Stock position per company and warehouse from the newest snapshot: items with stock, quantities on hand, committed and on order, and the stock value at average price in local currency.$seed$, $seed${}$seed$::jsonb, $seed$$seed$, NOW(), :'workspace_id'::uuid),
($seed$sap_b1_stock_on_hand$seed$, $seed$silver$seed$, $seed$sap_b1$seed$, $seed$["raw/sap_b1/OITW", "raw/sap_b1/OITM", "raw/sap_b1/OWHS", "raw/sap_b1/OADM"]$seed$::jsonb, $seed$
-- sap_b1_stock_on_hand  (silver)  cartridge: sap_b1
-- sources: ["raw/sap_b1/OITW", "raw/sap_b1/OITM", "raw/sap_b1/OWHS", "raw/sap_b1/OADM"]
-- description: Stock per company, item and warehouse from the newest OITW snapshot: on hand, committed, on order, available, and the stock value at the item's average price in local currency.

WITH snapshot AS (
    -- Newest run per company (all of its batches), never a mix of two runs.
    SELECT * EXCLUDE (_rn)
    FROM (
        SELECT s.*, ROW_NUMBER() OVER (PARTITION BY s._company, s.ItemCode, s.WhsCode ORDER BY s._extracted_at DESC) AS _rn
        FROM read_parquet('s3://{bucket}/raw/sap_b1/OITW/**/*.parquet',
                          hive_partitioning = true,
                          union_by_name   = true) s
        JOIN (
            SELECT _company, arg_max(regexp_replace(_run_id, '-b[0-9]+$', ''), _extracted_at) AS _run_key
            FROM read_parquet('s3://{bucket}/raw/sap_b1/OITW/**/*.parquet',
                          hive_partitioning = true,
                          union_by_name   = true)
            GROUP BY _company
        ) n ON n._company = s._company AND regexp_replace(s._run_id, '-b[0-9]+$', '') = n._run_key
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
warehouses AS (
    -- Newest run per company (all of its batches), never a mix of two runs.
    SELECT * EXCLUDE (_rn)
    FROM (
        SELECT s.*, ROW_NUMBER() OVER (PARTITION BY s._company, s.WhsCode ORDER BY s._extracted_at DESC) AS _rn
        FROM read_parquet('s3://{bucket}/raw/sap_b1/OWHS/**/*.parquet',
                          hive_partitioning = true,
                          union_by_name   = true) s
        JOIN (
            SELECT _company, arg_max(regexp_replace(_run_id, '-b[0-9]+$', ''), _extracted_at) AS _run_key
            FROM read_parquet('s3://{bucket}/raw/sap_b1/OWHS/**/*.parquet',
                          hive_partitioning = true,
                          union_by_name   = true)
            GROUP BY _company
        ) n ON n._company = s._company AND regexp_replace(s._run_id, '-b[0-9]+$', '') = n._run_key
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
    s._company                                          AS company,
    CAST(s.ItemCode AS VARCHAR)                         AS item_code,
    CAST(i.ItemName AS VARCHAR)                         AS item_name,
    CAST(i.ItmsGrpCod AS BIGINT)                        AS item_group_code,
    CAST(i.ManBtchNum AS VARCHAR) = 'Y'                 AS batch_managed,
    CAST(s.WhsCode AS VARCHAR)                          AS warehouse,
    CAST(w.WhsName AS VARCHAR)                          AS warehouse_name,
    CAST(s.OnHand AS DECIMAL(19,6))                     AS on_hand,
    CAST(s.IsCommited AS DECIMAL(19,6))                 AS committed,
    CAST(s.OnOrder AS DECIMAL(19,6))                    AS on_order,
    CAST(s.OnHand - COALESCE(s.IsCommited, 0) AS DECIMAL(19,6)) AS available,
    CAST(s.AvgPrice AS DECIMAL(19,6))                   AS avg_price,
    CAST(s.OnHand * COALESCE(s.AvgPrice, 0) AS DECIMAL(19,6)) AS stock_value_local,
    c.local_currency                                    AS local_currency,
    c.sys_currency                                      AS sys_currency,
    CAST(s.MinStock AS DECIMAL(19,6))                   AS min_stock,
    CAST(s.MaxStock AS DECIMAL(19,6))                   AS max_stock,
    s.load_date
FROM snapshot s
LEFT JOIN items i ON i._company = s._company AND i.ItemCode = s.ItemCode
LEFT JOIN warehouses w ON w._company = s._company AND w.WhsCode = s.WhsCode
LEFT JOIN company c ON c._company = s._company
ORDER BY company, item_code, warehouse
$seed$, $seed$Stock per company, item and warehouse from the newest OITW snapshot: on hand, committed, on order, available, and the stock value at the item's average price in local currency.$seed$, $seed${}$seed$::jsonb, $seed$$seed$, NOW(), :'workspace_id'::uuid),
($seed$sap_b1_transfer_lines$seed$, $seed$silver$seed$, $seed$sap_b1$seed$, $seed$["raw/sap_b1/OWTR", "raw/sap_b1/WTR1", "raw/sap_b1/OADM"]$seed$::jsonb, $seed$
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
$seed$, $seed$Inventory transfer lines with their header (OWTR/WTR1, ObjType 67): item, quantity, source and target warehouse; stock moves, money does not.$seed$, $seed${}$seed$::jsonb, $seed$$seed$, NOW(), :'workspace_id'::uuid),
($seed$sap_b1_wor1_latest$seed$, $seed$silver$seed$, $seed$sap_b1$seed$, $seed$["raw/sap_b1/WOR1"]$seed$::jsonb, $seed$
-- sap_b1_wor1_latest  (silver)  cartridge: sap_b1
-- sources: ["raw/sap_b1/WOR1"]
-- description: Components per production order, read through OWOR.

WITH versions AS (
    -- Estado ACTUAL por clave sobre TODO el histórico bronze; después solo
    -- las líneas que llevan la marca más reciente de su cabecera: una línea
    -- borrada del documento desaparece en cuanto la cabecera se relee.
    SELECT *
    FROM (
        SELECT *,
               ROW_NUMBER() OVER (
                   PARTITION BY _company, DocEntry, LineNum
                   ORDER BY _source_updated_at DESC NULLS LAST, load_date DESC, _extracted_at DESC
               ) AS _rn
        FROM read_parquet('s3://{bucket}/raw/sap_b1/WOR1/**/*.parquet',
                      hive_partitioning = true,
                      union_by_name   = true)
    )
    WHERE _rn = 1
),
current_lines AS (
    SELECT *
    FROM (
        SELECT *,
               MAX(_source_updated_at) OVER (PARTITION BY _company, DocEntry) AS _header_stamp
        FROM versions
    )
    WHERE _source_updated_at IS NULL OR _source_updated_at = _header_stamp
)
SELECT
    CAST(DocEntry AS BIGINT)                    AS doc_entry,
    CAST(LineNum AS BIGINT)                     AS line_num,
    CAST(ItemCode AS VARCHAR)                   AS item_code,
    CAST(BaseQty AS DECIMAL(19,6))              AS base_qty,
    CAST(PlannedQty AS DECIMAL(19,6))           AS planned_qty,
    CAST(IssuedQty AS DECIMAL(19,6))            AS issued_qty,
    CAST(wareHouse AS VARCHAR)                  AS ware_house,
    CAST(ItemType AS BIGINT)                    AS item_type,
    _company                              AS company,
    _source_updated_at                    AS source_updated_at,
    load_date
FROM current_lines
ORDER BY company, doc_entry, line_num
$seed$, $seed$Components per production order, read through OWOR.$seed$, $seed${}$seed$::jsonb, $seed$$seed$, NOW(), :'workspace_id'::uuid),
($seed$sap_b1_wtr1_latest$seed$, $seed$silver$seed$, $seed$sap_b1$seed$, $seed$["raw/sap_b1/WTR1"]$seed$::jsonb, $seed$
-- sap_b1_wtr1_latest  (silver)  cartridge: sap_b1
-- sources: ["raw/sap_b1/WTR1"]
-- description: Inventory transfer lines, read through OWTR: item, quantity, from and to warehouse.

WITH versions AS (
    -- Estado ACTUAL por clave sobre TODO el histórico bronze; después solo
    -- las líneas que llevan la marca más reciente de su cabecera: una línea
    -- borrada del documento desaparece en cuanto la cabecera se relee.
    SELECT *
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
current_lines AS (
    SELECT *
    FROM (
        SELECT *,
               MAX(_source_updated_at) OVER (PARTITION BY _company, DocEntry) AS _header_stamp
        FROM versions
    )
    WHERE _source_updated_at IS NULL OR _source_updated_at = _header_stamp
)
SELECT
    CAST(DocEntry AS BIGINT)                    AS doc_entry,
    CAST(LineNum AS BIGINT)                     AS line_num,
    CAST(LineStatus AS VARCHAR)                 AS line_status,
    CAST(ItemCode AS VARCHAR)                   AS item_code,
    CAST(Dscription AS VARCHAR)                 AS dscription,
    CAST(Quantity AS DECIMAL(19,6))             AS quantity,
    CAST(Price AS DECIMAL(19,6))                AS price,
    CAST(Currency AS VARCHAR)                   AS currency,
    CAST(Rate AS DECIMAL(19,6))                 AS rate,
    CAST(LineTotal AS DECIMAL(19,6))            AS line_total,
    CAST(TotalSumSy AS DECIMAL(19,6))           AS total_sum_sy,
    CAST(StockPrice AS DECIMAL(19,6))           AS stock_price,
    CAST(FromWhsCod AS VARCHAR)                 AS from_whs_cod,
    CAST(WhsCode AS VARCHAR)                    AS whs_code,
    CAST(ObjType AS VARCHAR)                    AS obj_type,
    CAST(VisOrder AS BIGINT)                    AS vis_order,
    _company                              AS company,
    _source_updated_at                    AS source_updated_at,
    load_date
FROM current_lines
ORDER BY company, doc_entry, line_num
$seed$, $seed$Inventory transfer lines, read through OWTR: item, quantity, from and to warehouse.$seed$, $seed${}$seed$::jsonb, $seed$$seed$, NOW(), :'workspace_id'::uuid)
ON CONFLICT (name) DO UPDATE
    SET layer = EXCLUDED.layer,
        sources = EXCLUDED.sources,
        sql_def = EXCLUDED.sql_def,
        description = EXCLUDED.description,
        updated_at = NOW();
