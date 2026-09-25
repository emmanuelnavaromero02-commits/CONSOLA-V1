"""Generate the curated silver datasets for Business One document lines.

One template, nine document pairs. Every row is a document line with its
header, the company's currencies made explicit (document, local, system),
the amounts in the three currencies, the cost the line carries
(StockPrice x Quantity) and the intercompany flag from the configured
partner mapping. Run: python cartridges/sap_b1/tools/generate_document_lines.py
"""
from __future__ import annotations

import pathlib
import sys

CART = pathlib.Path(__file__).resolve().parents[1]
OUT = pathlib.Path(sys.argv[1]) if len(sys.argv) > 1 else CART / "datasets"

# (dataset name, header table, line table, ObjType, business name, partner role)
PAIRS = [
    ("sap_b1_ar_invoice_lines", "OINV", "INV1", "13", "A/R invoice", "customer"),
    ("sap_b1_ar_credit_memo_lines", "ORIN", "RIN1", "14", "A/R credit memo", "customer"),
    ("sap_b1_delivery_lines", "ODLN", "DLN1", "15", "delivery", "customer"),
    ("sap_b1_return_lines", "ORDN", "RDN1", "16", "return", "customer"),
    ("sap_b1_sales_order_lines", "ORDR", "RDR1", "17", "sales order", "customer"),
    ("sap_b1_ap_invoice_lines", "OPCH", "PCH1", "18", "A/P invoice", "supplier"),
    ("sap_b1_ap_credit_memo_lines", "ORPC", "RPC1", "19", "A/P credit memo", "supplier"),
    ("sap_b1_goods_receipt_lines", "OPDN", "PDN1", "20", "goods receipt PO", "supplier"),
    ("sap_b1_purchase_order_lines", "OPOR", "POR1", "22", "purchase order", "supplier"),
]

TEMPLATE = """-- {name}  (silver)  cartridge: sap_b1
-- sources: ["raw/sap_b1/{header}", "raw/sap_b1/{line}", "raw/sap_b1/OADM", "raw/sap_b1/IntercompanyPartners"]
-- description: {business} lines with their header ({header}/{line}, ObjType {obj}): amounts in document, local and system currency, the cost the line carries, and the intercompany flag from the configured partner mapping. CANCELED is kept (N/Y/C); gold filters it.

WITH headers AS (
    -- Current version of every header per company over the whole bronze history.
    SELECT * EXCLUDE (_rn)
    FROM (
        SELECT *,
               ROW_NUMBER() OVER (
                   PARTITION BY _company, DocEntry
                   ORDER BY _source_updated_at DESC NULLS LAST, load_date DESC, _extracted_at DESC
               ) AS _rn
        FROM read_parquet('s3://{{bucket}}/raw/sap_b1/{header}/**/*.parquet',
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
        FROM read_parquet('s3://{{bucket}}/raw/sap_b1/{line}/**/*.parquet',
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
        FROM read_parquet('s3://{{bucket}}/raw/sap_b1/OADM/**/*.parquet',
                          hive_partitioning = true,
                          union_by_name   = true) s
        JOIN (
            SELECT _company, arg_max(regexp_replace(_run_id, '-b[0-9]+$', ''), _extracted_at) AS _run_key
            FROM read_parquet('s3://{{bucket}}/raw/sap_b1/OADM/**/*.parquet',
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
        FROM read_parquet('s3://{{bucket}}/raw/sap_b1/IntercompanyPartners/**/*.parquet',
                          hive_partitioning = true,
                          union_by_name   = true) s
        JOIN (
            SELECT _company, arg_max(regexp_replace(_run_id, '-b[0-9]+$', ''), _extracted_at) AS _run_key
            FROM read_parquet('s3://{{bucket}}/raw/sap_b1/IntercompanyPartners/**/*.parquet',
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
"""


def render(name: str, header: str, line: str, obj: str, business: str, role: str) -> str:
    return TEMPLATE.format(name=name, header=header, line=line, obj=obj, business=business, role=role)


if __name__ == "__main__":
    OUT.mkdir(parents=True, exist_ok=True)
    for name, header, line, obj, business, role in PAIRS:
        (OUT / f"{name}.sql").write_text(render(name, header, line, obj, business, role))
    print(len(PAIRS), "document line datasets written")
