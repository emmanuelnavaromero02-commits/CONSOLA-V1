-- sap_s4hana_billingdocumentitem_latest  (silver)  cartridge: sap_s4hana
-- sources: ["raw/sap_s4hana/BillingDocumentItem"]
-- description: Última extracción de líneas de factura de venta (A_BillingDocumentItem).

WITH latest AS (
    -- Estado ACTUAL por clave de negocio sobre TODO el historico bronze.
    -- BillingDocumentItem es incremental: cada load_date trae solo los cambios desde el
    -- watermark, asi que quedarse con la ultima particion (MAX(load_date))
    -- colapsaba la poblacion al delta del dia — perdida silenciosa de datos.
    -- Dedupe determinista: la ultima version de cada fila (BillingDocument, BillingDocumentItem).
    -- Limite conocido: un borrado fisico en la fuente no se refleja hasta un
    -- full load (el incremental OData no acarrea deletes).
    SELECT * EXCLUDE (_rn)
    FROM (
        SELECT *,
               ROW_NUMBER() OVER (
                   PARTITION BY BillingDocument, BillingDocumentItem
                   ORDER BY load_date DESC, LastChangeDateTime DESC NULLS LAST
               ) AS _rn
        FROM read_parquet('s3://{bucket}/raw/sap_s4hana/BillingDocumentItem/**/*.parquet',
                          hive_partitioning = true,
                          union_by_name   = true)
    )
    WHERE _rn = 1
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
