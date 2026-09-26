-- sap_b1_intercompany_reconciliation_month  (gold)  cartridge: sap_b1
-- sources: ["silver/sap_b1/sap_b1_ap_invoice_lines", "silver/sap_b1/sap_b1_ar_invoice_lines"]
-- description: What consolidation eliminates, proven on both sides: per seller, buyer and month, the seller's invoices to the buyer against the buyer's supplier invoices from the seller, and the difference. A non-zero difference is a capture or timing gap to explain, not something to hide.

WITH sold AS (
    SELECT company AS seller, counterparty_company AS buyer, doc_month, local_currency,
           COUNT(DISTINCT doc_entry) AS seller_invoices,
           SUM(amount_local_net)     AS sold_local
    FROM read_parquet('s3://{bucket}/silver/sap_b1/sap_b1_ar_invoice_lines/**/*.parquet')
    WHERE canceled = 'N' AND is_intercompany
    GROUP BY 1, 2, 3, 4
),
bought AS (
    SELECT counterparty_company AS seller, company AS buyer, doc_month, local_currency,
           COUNT(DISTINCT doc_entry) AS buyer_invoices,
           SUM(amount_local_net)     AS bought_local
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
