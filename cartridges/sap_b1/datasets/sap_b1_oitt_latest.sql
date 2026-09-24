-- sap_b1_oitt_latest  (silver)  cartridge: sap_b1
-- sources: ["raw/sap_b1/OITT"]
-- description: Bill of materials headers (production BOMs).

WITH latest AS (
    -- Estado ACTUAL por clave sobre TODO el histórico bronze. La entidad es
    -- incremental: quedarse con MAX(load_date) colapsaría la población al
    -- delta del día. Un borrado en la fuente no se refleja hasta una carga
    -- completa; ver README del cartucho.
    SELECT *
    FROM (
        SELECT *,
               ROW_NUMBER() OVER (
                   PARTITION BY _company, Code
                   ORDER BY _source_updated_at DESC NULLS LAST, load_date DESC, _extracted_at DESC
               ) AS _rn
        FROM read_parquet('s3://{bucket}/raw/sap_b1/OITT/**/*.parquet',
                      hive_partitioning = true,
                      union_by_name   = true)
    )
    WHERE _rn = 1
)
SELECT
    CAST(Code AS VARCHAR)                       AS code,
    CAST(TreeType AS VARCHAR)                   AS tree_type,
    CAST(Qauntity AS DECIMAL(19,6))             AS qauntity,
    CAST(ToWH AS VARCHAR)                       AS to_wh,
    CAST(PriceList AS BIGINT)                   AS price_list,
    CAST(CreateDate AS TIMESTAMP)               AS create_date,
    CAST(UpdateDate AS TIMESTAMP)               AS update_date,
    CAST(UpdateTS AS BIGINT)                    AS update_ts,
    _company                              AS company,
    _source_updated_at                    AS source_updated_at,
    load_date
FROM latest
ORDER BY company, code
