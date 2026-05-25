-- sap_s4hana_salesorder_latest  (silver)  cartridge: sap_s4hana
-- sources: ["raw/sap_s4hana/SalesOrder"]
-- description: Última extracción de cabeceras de pedido de venta (A_SalesOrder). SoldToParty es el código de cliente (en claro: A_SalesOrder no tiene regla de protección).

WITH latest AS (
    SELECT *
    FROM read_parquet('s3://{bucket}/raw/sap_s4hana/SalesOrder/**/*.parquet',
                      hive_partitioning = true,
                      union_by_name   = true)
    WHERE load_date = (SELECT MAX(load_date)
                       FROM read_parquet('s3://{bucket}/raw/sap_s4hana/SalesOrder/**/*.parquet',
                                          hive_partitioning = true))
)
SELECT
    SalesOrder                          AS sales_order,
    SalesOrderType                      AS sales_order_type,
    SoldToParty                         AS sold_to_party,
    SalesOrganization                   AS sales_organization,
    CAST(SalesOrderDate AS DATE)        AS sales_order_date,
    OverallSDProcessStatus              AS overall_status,
    CAST(TotalNetAmount AS DECIMAL(15,2)) AS total_net_amount,
    TransactionCurrency                 AS currency,
    CAST(LastChangeDateTime AS TIMESTAMP) AS last_change,
    load_date
FROM latest
ORDER BY sales_order
