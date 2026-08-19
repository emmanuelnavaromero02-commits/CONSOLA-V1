-- sap_s4hana_billingdocument_latest  (silver)  cartridge: sap_s4hana
-- sources: ["raw/sap_s4hana/BillingDocument"]
-- description: Última extracción de cabeceras de factura de venta (A_BillingDocument). SoldToParty es el código de cliente en claro.

WITH latest AS (
    -- Estado ACTUAL por clave de negocio sobre TODO el historico bronze.
    -- BillingDocument es incremental: cada load_date trae solo los cambios desde el
    -- watermark, asi que quedarse con la ultima particion (MAX(load_date))
    -- colapsaba la poblacion al delta del dia — perdida silenciosa de datos.
    -- Dedupe determinista: la ultima version de cada fila (BillingDocument).
    -- Limite conocido: un borrado fisico en la fuente no se refleja hasta un
    -- full load (el incremental OData no acarrea deletes).
    SELECT * EXCLUDE (_rn)
    FROM (
        SELECT *,
               ROW_NUMBER() OVER (
                   PARTITION BY BillingDocument
                   ORDER BY load_date DESC, LastChangeDateTime DESC NULLS LAST
               ) AS _rn
        FROM read_parquet('s3://{bucket}/raw/sap_s4hana/BillingDocument/**/*.parquet',
                          hive_partitioning = true,
                          union_by_name   = true)
    )
    WHERE _rn = 1
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
