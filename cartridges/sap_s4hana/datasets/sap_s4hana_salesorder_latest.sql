-- sap_s4hana_salesorder_latest  (silver)  cartridge: sap_s4hana
-- sources: ["raw/sap_s4hana/SalesOrder"]
-- description: Última extracción de cabeceras de pedido de venta (A_SalesOrder). SoldToParty es el código de cliente (en claro: A_SalesOrder no tiene regla de protección).

WITH latest AS (
    -- Estado ACTUAL por clave de negocio sobre TODO el historico bronze.
    -- SalesOrder es incremental: cada load_date trae solo los cambios desde el
    -- watermark, asi que quedarse con la ultima particion (MAX(load_date))
    -- colapsaba la poblacion al delta del dia — perdida silenciosa de datos.
    -- Dedupe determinista: la ultima version de cada fila (SalesOrder).
    -- Limite conocido: un borrado fisico en la fuente no se refleja hasta un
    -- full load (el incremental OData no acarrea deletes).
    SELECT * EXCLUDE (_rn)
    FROM (
        SELECT *,
               ROW_NUMBER() OVER (
                   PARTITION BY SalesOrder
                   ORDER BY load_date DESC, LastChangeDateTime DESC NULLS LAST
               ) AS _rn
        FROM read_parquet('s3://{bucket}/raw/sap_s4hana/SalesOrder/**/*.parquet',
                          hive_partitioning = true,
                          union_by_name   = true)
    )
    WHERE _rn = 1
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
