-- sap_s4hana_billingdocumentitem_latest  (silver)  cartridge: sap_s4hana
-- sources: ["raw/sap_s4hana/BillingDocumentItem"]
-- description: Última extracción de líneas de factura de venta (A_BillingDocumentItem).

WITH latest AS (
    SELECT *
    FROM read_parquet('s3://{bucket}/raw/sap_s4hana/BillingDocumentItem/**/*.parquet',
                      hive_partitioning = true,
                      union_by_name   = true)
    WHERE load_date = (SELECT MAX(load_date)
                       FROM read_parquet('s3://{bucket}/raw/sap_s4hana/BillingDocumentItem/**/*.parquet',
                                          hive_partitioning = true))
)
SELECT
    BillingDocument                     AS billing_document,
    BillingDocumentItem                 AS billing_document_item,
    Material                            AS material,
    CAST(BillingQuantity AS DECIMAL(15,3)) AS billing_quantity,
    CAST(NetAmount AS DECIMAL(15,2))    AS net_amount,
    load_date
FROM latest
ORDER BY billing_document, billing_document_item
