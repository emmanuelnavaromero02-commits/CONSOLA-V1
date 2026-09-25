-- sap_b1_ibt1_latest  (silver)  cartridge: sap_b1
-- sources: ["raw/sap_b1/IBT1"]
-- description: Batch quantities per document line, Direction 0 in / 1 out; rows never change, LogEntry (identity, validate in HANA) is the watermark and page key.

WITH latest AS (
    -- Las filas nunca cambian en la fuente; el mismo registro puede llegar
    -- más de una vez (carga completa + incremental), así que se deduplica
    -- por clave sobre todo el histórico bronze.
    SELECT *
    FROM (
        SELECT *,
               ROW_NUMBER() OVER (
                   PARTITION BY _company, LogEntry
                   ORDER BY load_date DESC, _extracted_at DESC
               ) AS _rn
        FROM read_parquet('s3://{bucket}/raw/sap_b1/IBT1/**/*.parquet',
                      hive_partitioning = true,
                      union_by_name   = true)
    )
    WHERE _rn = 1
)
SELECT
    CAST(LogEntry AS BIGINT)                    AS log_entry,
    CAST(ItemCode AS VARCHAR)                   AS item_code,
    CAST(BatchNum AS VARCHAR)                   AS batch_num,
    CAST(WhsCode AS VARCHAR)                    AS whs_code,
    CAST(BaseType AS BIGINT)                    AS base_type,
    CAST(BaseEntry AS BIGINT)                   AS base_entry,
    CAST(BaseLinNum AS BIGINT)                  AS base_lin_num,
    CAST(Quantity AS DECIMAL(19,6))             AS quantity,
    CAST(Direction AS BIGINT)                   AS direction,
    CAST(DocDate AS TIMESTAMP)                  AS doc_date,
    _company                              AS company,
    _source_updated_at                    AS source_updated_at,
    load_date
FROM latest
ORDER BY company, log_entry
