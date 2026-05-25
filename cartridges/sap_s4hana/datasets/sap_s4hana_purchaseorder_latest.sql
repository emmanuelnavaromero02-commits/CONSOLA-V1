-- sap_s4hana_purchaseorder_latest  (silver)  cartridge: sap_s4hana
-- sources: ["raw/sap_s4hana/PurchaseOrder"]
-- description: Última extracción de cabeceras de orden de compra (A_PurchaseOrder). Supplier es el código de proveedor en claro.

WITH latest AS (
    SELECT *
    FROM read_parquet('s3://{bucket}/raw/sap_s4hana/PurchaseOrder/**/*.parquet',
                      hive_partitioning = true,
                      union_by_name   = true)
    WHERE load_date = (SELECT MAX(load_date)
                       FROM read_parquet('s3://{bucket}/raw/sap_s4hana/PurchaseOrder/**/*.parquet',
                                          hive_partitioning = true))
)
SELECT
    PurchaseOrder                       AS purchase_order,
    PurchaseOrderType                   AS purchase_order_type,
    Supplier                            AS supplier,
    CompanyCode                         AS company_code,
    CAST(PurchaseOrderDate AS DATE)     AS purchase_order_date,
    DocumentCurrency                    AS currency,
    CAST(LastChangeDateTime AS TIMESTAMP) AS last_change,
    load_date
FROM latest
ORDER BY purchase_order
