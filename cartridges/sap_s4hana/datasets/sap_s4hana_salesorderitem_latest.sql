-- sap_s4hana_salesorderitem_latest  (silver)  cartridge: sap_s4hana
-- sources: ["raw/sap_s4hana/SalesOrderItem"]
-- description: Última extracción de líneas de pedido de venta (A_SalesOrderItem).

WITH latest AS (
    -- Estado ACTUAL por clave de negocio sobre TODO el historico bronze.
    -- SalesOrderItem es incremental: cada load_date trae solo los cambios desde el
    -- watermark, asi que quedarse con la ultima particion (MAX(load_date))
    -- colapsaba la poblacion al delta del dia — perdida silenciosa de datos.
    -- Dedupe determinista: la ultima version de cada fila (SalesOrder, SalesOrderItem).
    -- Limite conocido: un borrado fisico en la fuente no se refleja hasta un
    -- full load (el incremental OData no acarrea deletes).
    SELECT * EXCLUDE (_rn)
    FROM (
        SELECT *,
               ROW_NUMBER() OVER (
                   PARTITION BY SalesOrder, SalesOrderItem
                   ORDER BY load_date DESC, LastChangeDateTime DESC NULLS LAST
               ) AS _rn
        FROM read_parquet('s3://{bucket}/raw/sap_s4hana/SalesOrderItem/**/*.parquet',
                          hive_partitioning = true,
                          union_by_name   = true)
    )
    WHERE _rn = 1
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
