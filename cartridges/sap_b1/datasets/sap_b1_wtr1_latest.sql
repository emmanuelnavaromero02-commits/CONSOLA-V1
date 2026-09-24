-- sap_b1_wtr1_latest  (silver)  cartridge: sap_b1
-- sources: ["raw/sap_b1/WTR1"]
-- description: Inventory transfer lines, read through OWTR: item, quantity, from and to warehouse.

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
        FROM read_parquet('s3://{bucket}/raw/sap_b1/WTR1/**/*.parquet',
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
    CAST(LineStatus AS VARCHAR)                 AS line_status,
    CAST(ItemCode AS VARCHAR)                   AS item_code,
    CAST(Dscription AS VARCHAR)                 AS dscription,
    CAST(Quantity AS DECIMAL(19,6))             AS quantity,
    CAST(Price AS DECIMAL(19,6))                AS price,
    CAST(Currency AS VARCHAR)                   AS currency,
    CAST(Rate AS DECIMAL(19,6))                 AS rate,
    CAST(LineTotal AS DECIMAL(19,6))            AS line_total,
    CAST(TotalSumSy AS DECIMAL(19,6))           AS total_sum_sy,
    CAST(StockPrice AS DECIMAL(19,6))           AS stock_price,
    CAST(FromWhsCod AS VARCHAR)                 AS from_whs_cod,
    CAST(WhsCode AS VARCHAR)                    AS whs_code,
    CAST(ObjType AS VARCHAR)                    AS obj_type,
    CAST(VisOrder AS BIGINT)                    AS vis_order,
    _company                              AS company,
    _source_updated_at                    AS source_updated_at,
    load_date
FROM current_lines
ORDER BY company, doc_entry, line_num
