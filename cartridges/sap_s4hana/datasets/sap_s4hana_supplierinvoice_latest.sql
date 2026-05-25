-- sap_s4hana_supplierinvoice_latest  (silver)  cartridge: sap_s4hana
-- sources: ["raw/sap_s4hana/SupplierInvoice"]
-- description: Última extracción de facturas de proveedor (A_SupplierInvoice). InvoicingParty llega shadowed desde bronze.

WITH latest AS (
    SELECT *
    FROM read_parquet('s3://{bucket}/raw/sap_s4hana/SupplierInvoice/**/*.parquet',
                      hive_partitioning = true,
                      union_by_name   = true)
    WHERE load_date = (SELECT MAX(load_date)
                       FROM read_parquet('s3://{bucket}/raw/sap_s4hana/SupplierInvoice/**/*.parquet',
                                          hive_partitioning = true))
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
