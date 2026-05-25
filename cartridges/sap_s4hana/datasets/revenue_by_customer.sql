-- revenue_by_customer  (gold)  cartridge: sap_s4hana
-- sources: ["raw/sap_s4hana/BillingDocument", "raw/sap_s4hana/BillingDocumentItem"]
-- description: Ingresos por cliente y mes a partir de facturas de venta. Ranking de clientes por revenue.

WITH inv AS (
    SELECT customer_code, billing_document, billing_document_date, net_amount, currency
    FROM read_parquet('s3://{bucket}/silver/sap_s4hana/sap_s4hana_invoices_full/**/*.parquet')
    WHERE billing_document_date IS NOT NULL
)
SELECT
    customer_code                                            AS customer_code,
    CAST(DATE_TRUNC('month', billing_document_date) AS DATE) AS revenue_month,
    currency                                                 AS currency,
    ROUND(SUM(net_amount), 2)                                AS revenue,
    COUNT(DISTINCT billing_document)                         AS invoice_count
FROM inv
GROUP BY customer_code, DATE_TRUNC('month', billing_document_date), currency
ORDER BY revenue_month DESC, revenue DESC
