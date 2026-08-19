-- sap_s4hana_supplierinvoice_latest  (silver)  cartridge: sap_s4hana
-- sources: ["raw/sap_s4hana/SupplierInvoice"]
-- description: Última extracción de facturas de proveedor (A_SupplierInvoice). InvoicingParty llega shadowed desde bronze.

WITH latest AS (
    -- Estado ACTUAL por clave de negocio sobre TODO el historico bronze.
    -- SupplierInvoice es incremental: cada load_date trae solo los cambios desde el
    -- watermark, asi que quedarse con la ultima particion (MAX(load_date))
    -- colapsaba la poblacion al delta del dia — perdida silenciosa de datos.
    -- Dedupe determinista: la ultima version de cada fila (SupplierInvoice, CompanyCode).
    -- Limite conocido: un borrado fisico en la fuente no se refleja hasta un
    -- full load (el incremental OData no acarrea deletes).
    SELECT * EXCLUDE (_rn)
    FROM (
        SELECT *,
               ROW_NUMBER() OVER (
                   PARTITION BY SupplierInvoice, CompanyCode
                   ORDER BY load_date DESC, LastChangeDateTime DESC NULLS LAST
               ) AS _rn
        FROM read_parquet('s3://{bucket}/raw/sap_s4hana/SupplierInvoice/**/*.parquet',
                          hive_partitioning = true,
                          union_by_name   = true)
    )
    WHERE _rn = 1
)
SELECT
    SupplierInvoice                     AS supplier_invoice,
    InvoicingParty                      AS invoicing_party,    -- shadowed en bronze (FK)
    CompanyCode                         AS company_code,
    CAST(DocumentDate AS DATE)          AS document_date,
    CAST(InvoiceGrossAmount AS DECIMAL(15,2)) AS invoice_gross_amount,
    DocumentCurrency                    AS currency,
    load_date
FROM latest
ORDER BY supplier_invoice
