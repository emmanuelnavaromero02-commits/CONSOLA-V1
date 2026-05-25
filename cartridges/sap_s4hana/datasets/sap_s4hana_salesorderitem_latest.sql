-- sap_s4hana_salesorderitem_latest  (silver)  cartridge: sap_s4hana
-- sources: ["raw/sap_s4hana/SalesOrderItem"]
-- description: Última extracción de líneas de pedido de venta (A_SalesOrderItem).

WITH latest AS (
    SELECT *
    FROM read_parquet('s3://{bucket}/raw/sap_s4hana/SalesOrderItem/**/*.parquet',
                      hive_partitioning = true,
                      union_by_name   = true)
    WHERE load_date = (SELECT MAX(load_date)
                       FROM read_parquet('s3://{bucket}/raw/sap_s4hana/SalesOrderItem/**/*.parquet',
                                          hive_partitioning = true))
)
SELECT
    SalesOrder                          AS sales_order,
    SalesOrderItem                      AS sales_order_item,
    Material                            AS material,
    CAST(RequestedQuantity AS DECIMAL(15,3)) AS requested_quantity,
    CAST(NetAmount AS DECIMAL(15,2))    AS net_amount,
    Plant                               AS plant,
    load_date
FROM latest
ORDER BY sales_order, sales_order_item
