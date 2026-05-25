-- sap_s4hana_invoices_full  (silver)  cartridge: sap_s4hana
-- sources: ["raw/sap_s4hana/BillingDocument", "raw/sap_s4hana/BillingDocumentItem"]
-- description: Facturas de venta a nivel línea, enriquecidas con la cabecera (cliente, fecha, moneda, condiciones de pago). Una fila por línea.

WITH docs AS (
    SELECT billing_document, billing_document_date, sold_to_party,
           total_net_amount, currency, payment_terms
    FROM read_parquet('s3://{bucket}/silver/sap_s4hana/sap_s4hana_billingdocument_latest/**/*.parquet')
),
items AS (
    SELECT billing_document, billing_document_item, material, billing_quantity, net_amount
    FROM read_parquet('s3://{bucket}/silver/sap_s4hana/sap_s4hana_billingdocumentitem_latest/**/*.parquet')
)
SELECT
    d.billing_document        AS billing_document,
    i.billing_document_item   AS billing_document_item,
    d.billing_document_date   AS billing_document_date,
    d.sold_to_party           AS customer_code,
    i.material                AS material,
    i.billing_quantity        AS billing_quantity,
    i.net_amount              AS net_amount,
    d.total_net_amount        AS document_net_amount,
    d.currency                AS currency,
    d.payment_terms           AS payment_terms
FROM docs d
LEFT JOIN items i ON i.billing_document = d.billing_document
ORDER BY d.billing_document, i.billing_document_item
