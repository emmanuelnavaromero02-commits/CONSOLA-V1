-- sap_b1_wor1_latest  (silver)  cartridge: sap_b1
-- sources: ["raw/sap_b1/WOR1"]
-- description: Components per production order, read through OWOR.

WITH versions AS (
    -- Estado ACTUAL por clave sobre TODO el histórico bronze; después solo
    -- las líneas que llevan la marca más reciente de su cabecera: una línea
    -- borrada del documento desaparece en cuanto la cabecera se relee.
    SELECT *
    FROM (
        SELECT *,
               ROW_NUMBER() OVER (
                   PARTITION BY _company, DocEntry, LineNum
                   ORDER BY _source_updated_at DESC NULLS LAST, load_date DESC, _extracted_at DESC
               ) AS _rn
        FROM read_parquet('s3://{bucket}/raw/sap_b1/WOR1/**/*.parquet',
                      hive_partitioning = true,
                      union_by_name   = true)
    )
    WHERE _rn = 1
),
current_lines AS (
    SELECT *
    FROM (
        SELECT *,
               MAX(_source_updated_at) OVER (PARTITION BY _company, DocEntry) AS _header_stamp
        FROM versions
    )
    WHERE _source_updated_at IS NULL OR _source_updated_at = _header_stamp
)
SELECT
    CAST(DocEntry AS BIGINT)                    AS doc_entry,
    CAST(LineNum AS BIGINT)                     AS line_num,
    CAST(ItemCode AS VARCHAR)                   AS item_code,
    CAST(BaseQty AS DECIMAL(19,6))              AS base_qty,
    CAST(PlannedQty AS DECIMAL(19,6))           AS planned_qty,
    CAST(IssuedQty AS DECIMAL(19,6))            AS issued_qty,
    CAST(wareHouse AS VARCHAR)                  AS ware_house,
    CAST(ItemType AS BIGINT)                    AS item_type,
    _company                              AS company,
    _source_updated_at                    AS source_updated_at,
    load_date
FROM current_lines
ORDER BY company, doc_entry, line_num
