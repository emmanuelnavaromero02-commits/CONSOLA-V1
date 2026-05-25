-- inventory_movement_summary  (gold)  cartridge: sap_s4hana
-- sources: ["raw/sap_s4hana/MaterialDocumentHeader"]
-- description: Resumen mensual de movimientos de inventario (conteo de documentos de material por mes de contabilización).

-- TODO: las cantidades por material/clase de movimiento viven en
-- A_MaterialDocumentItem (no extraído). Este resumen es a nivel cabecera:
-- número de documentos de material por mes.
WITH mdh AS (
    SELECT
        MaterialDocument            AS material_document,
        CAST(PostingDate AS DATE)   AS posting_date
    FROM read_parquet('s3://{bucket}/raw/sap_s4hana/MaterialDocumentHeader/**/*.parquet',
                      hive_partitioning = true,
                      union_by_name   = true)
    WHERE load_date = (SELECT MAX(load_date)
                       FROM read_parquet('s3://{bucket}/raw/sap_s4hana/MaterialDocumentHeader/**/*.parquet',
                                          hive_partitioning = true))
)
SELECT
    CAST(DATE_TRUNC('month', posting_date) AS DATE) AS posting_month,
    COUNT(DISTINCT material_document)               AS movement_documents
FROM mdh
WHERE posting_date IS NOT NULL
GROUP BY DATE_TRUNC('month', posting_date)
ORDER BY posting_month DESC
