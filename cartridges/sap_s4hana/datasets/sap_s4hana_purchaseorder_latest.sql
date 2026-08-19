-- sap_s4hana_purchaseorder_latest  (silver)  cartridge: sap_s4hana
-- sources: ["raw/sap_s4hana/PurchaseOrder"]
-- description: Última extracción de cabeceras de orden de compra (A_PurchaseOrder). Supplier es el código de proveedor en claro.

WITH latest AS (
    -- Estado ACTUAL por clave de negocio sobre TODO el historico bronze.
    -- PurchaseOrder es incremental: cada load_date trae solo los cambios desde el
    -- watermark, asi que quedarse con la ultima particion (MAX(load_date))
    -- colapsaba la poblacion al delta del dia — perdida silenciosa de datos.
    -- Dedupe determinista: la ultima version de cada fila (PurchaseOrder).
    -- Limite conocido: un borrado fisico en la fuente no se refleja hasta un
    -- full load (el incremental OData no acarrea deletes).
    SELECT * EXCLUDE (_rn)
    FROM (
        SELECT *,
               ROW_NUMBER() OVER (
                   PARTITION BY PurchaseOrder
                   ORDER BY load_date DESC, LastChangeDateTime DESC NULLS LAST
               ) AS _rn
        FROM read_parquet('s3://{bucket}/raw/sap_s4hana/PurchaseOrder/**/*.parquet',
                          hive_partitioning = true,
                          union_by_name   = true)
    )
    WHERE _rn = 1
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
