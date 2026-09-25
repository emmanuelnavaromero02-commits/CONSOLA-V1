-- overdue_billing  (gold)  cartridge: sap_s4hana
-- sources: ["raw/sap_s4hana/BillingDocument", "silver/sap_s4hana/sap_s4hana_billingdocument_latest"]
-- description: Cartera vencida (aging) de facturas de venta por antigüedad estimada. El estado de pago real no está disponible; el vencimiento se aproxima por fecha.

-- TODO: el estado pagado/no pagado vive en partidas abiertas de FI
-- (A_OperationalAcctgDocItem, no extraído). Aquí el vencimiento se estima como
-- billing_document_date + 30 días y se reporta solo el aging por fecha.
WITH bills AS (
    SELECT billing_document, sold_to_party, billing_document_date, total_net_amount, currency
    FROM read_parquet('s3://{bucket}/silver/sap_s4hana/sap_s4hana_billingdocument_latest/**/*.parquet')
    WHERE billing_document_date IS NOT NULL
)
SELECT
    billing_document                                          AS billing_document,
    sold_to_party                                            AS customer_code,
    billing_document_date                                    AS billing_document_date,
    CAST(billing_document_date + INTERVAL 30 DAY AS DATE)    AS estimated_due_date,
    ROUND(total_net_amount, 2)                               AS amount,
    currency                                                 AS currency,
    CAST(DATE_DIFF('day', billing_document_date + INTERVAL 30 DAY, CURRENT_DATE) AS INTEGER) AS days_overdue
FROM bills
WHERE CAST(billing_document_date + INTERVAL 30 DAY AS DATE) < CURRENT_DATE
ORDER BY days_overdue DESC, amount DESC
