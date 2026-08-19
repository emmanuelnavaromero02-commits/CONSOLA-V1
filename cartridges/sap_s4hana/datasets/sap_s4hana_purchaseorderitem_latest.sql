-- sap_s4hana_purchaseorderitem_latest  (silver)  cartridge: sap_s4hana
-- sources: ["raw/sap_s4hana/PurchaseOrderItem"]
-- description: Última extracción de líneas de orden de compra (A_PurchaseOrderItem).

WITH latest AS (
    -- Estado ACTUAL por clave de negocio sobre TODO el historico bronze.
    -- PurchaseOrderItem es incremental: cada load_date trae solo los cambios desde el
    -- watermark, asi que quedarse con la ultima particion (MAX(load_date))
    -- colapsaba la poblacion al delta del dia — perdida silenciosa de datos.
    -- Dedupe determinista: la ultima version de cada fila (PurchaseOrder, PurchaseOrderItem).
    -- Limite conocido: un borrado fisico en la fuente no se refleja hasta un
    -- full load (el incremental OData no acarrea deletes).
    SELECT * EXCLUDE (_rn)
    FROM (
        SELECT *,
               ROW_NUMBER() OVER (
                   PARTITION BY PurchaseOrder, PurchaseOrderItem
                   ORDER BY load_date DESC, LastChangeDateTime DESC NULLS LAST
               ) AS _rn
        FROM read_parquet('s3://{bucket}/raw/sap_s4hana/PurchaseOrderItem/**/*.parquet',
                          hive_partitioning = true,
                          union_by_name   = true)
    )
    WHERE _rn = 1
)
SELECT
    PurchaseOrder                       AS purchase_order,
    PurchaseOrderItem                   AS purchase_order_item,
    Material                            AS material,
    CAST(OrderQuantity AS DECIMAL(15,3)) AS order_quantity,
    CAST(NetPriceAmount AS DECIMAL(15,2)) AS net_price_amount,
    Plant                               AS plant,
    load_date
FROM latest
ORDER BY purchase_order, purchase_order_item
