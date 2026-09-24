-- sap_b1_itt1_latest  (silver)  cartridge: sap_b1
-- sources: ["raw/sap_b1/ITT1"]
-- description: Components per BOM; read through OITT so an edited BOM brings all its lines.

WITH versions AS (
    -- Estado ACTUAL por clave sobre TODO el histórico bronze; después solo
    -- las líneas que llevan la marca más reciente de su cabecera: una línea
    -- borrada del documento desaparece en cuanto la cabecera se relee.
    SELECT *
    FROM (
        SELECT *,
               ROW_NUMBER() OVER (
                   PARTITION BY _company, Father, ChildNum
                   ORDER BY _source_updated_at DESC NULLS LAST, load_date DESC, _extracted_at DESC
               ) AS _rn
        FROM read_parquet('s3://{bucket}/raw/sap_b1/ITT1/**/*.parquet',
                      hive_partitioning = true,
                      union_by_name   = true)
    )
    WHERE _rn = 1
),
current_lines AS (
    SELECT *
    FROM (
        SELECT *,
               MAX(_source_updated_at) OVER (PARTITION BY _company, Father) AS _header_stamp
        FROM versions
    )
    WHERE _source_updated_at IS NULL OR _source_updated_at = _header_stamp
)
SELECT
    CAST(Father AS VARCHAR)                     AS father,
    CAST(ChildNum AS BIGINT)                    AS child_num,
    CAST(Code AS VARCHAR)                       AS code,
    CAST(Quantity AS DECIMAL(19,6))             AS quantity,
    CAST(Warehouse AS VARCHAR)                  AS warehouse,
    CAST(IssueMthd AS VARCHAR)                  AS issue_mthd,
    CAST(PriceList AS BIGINT)                   AS price_list,
    _company                              AS company,
    _source_updated_at                    AS source_updated_at,
    load_date
FROM current_lines
ORDER BY company, father, child_num
