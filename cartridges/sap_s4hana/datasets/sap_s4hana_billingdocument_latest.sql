-- sap_s4hana_billingdocument_latest  (silver)  cartridge: sap_s4hana
-- sources: ["raw/sap_s4hana/BillingDocument"]
-- description: Última extracción de cabeceras de factura de venta (A_BillingDocument). SoldToParty es el código de cliente en claro.

WITH latest AS (
    SELECT *
    FROM read_parquet('s3://{bucket}/raw/sap_s4hana/BillingDocument/**/*.parquet',
                      hive_partitioning = true,
                      union_by_name   = true)
    WHERE load_date = (SELECT MAX(load_date)
                       FROM read_parquet('s3://{bucket}/raw/sap_s4hana/BillingDocument/**/*.parquet',
                                          hive_partitioning = true))
)
SELECT
    BillingDocument                     AS billing_document,
    CAST(BillingDocumentDate AS DATE)   AS billing_document_date,
    SoldToParty                         AS sold_to_party,
    CAST(TotalNetAmount AS DECIMAL(15,2)) AS total_net_amount,
    TransactionCurrency                 AS currency,
    PaymentTerms                        AS payment_terms,
    CAST(LastChangeDateTime AS TIMESTAMP) AS last_change,
    load_date
FROM latest
ORDER BY billing_document
