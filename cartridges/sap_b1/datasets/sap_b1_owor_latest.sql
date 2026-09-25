-- sap_b1_owor_latest  (silver)  cartridge: sap_b1
-- sources: ["raw/sap_b1/OWOR"]
-- description: Production order headers: planned, completed and rejected quantities, status and dates.

WITH latest AS (
    -- Estado ACTUAL por clave sobre TODO el histórico bronze. La entidad es
    -- incremental: quedarse con MAX(load_date) colapsaría la población al
    -- delta del día. Un borrado en la fuente no se refleja hasta una carga
    -- completa; ver README del cartucho.
    SELECT *
    FROM (
        SELECT *,
               ROW_NUMBER() OVER (
                   PARTITION BY _company, DocEntry
                   ORDER BY _source_updated_at DESC NULLS LAST, load_date DESC, _extracted_at DESC
               ) AS _rn
        FROM read_parquet('s3://{bucket}/raw/sap_b1/OWOR/**/*.parquet',
                      hive_partitioning = true,
                      union_by_name   = true)
    )
    WHERE _rn = 1
)
SELECT
    CAST(DocEntry AS BIGINT)                    AS doc_entry,
    CAST(DocNum AS BIGINT)                      AS doc_num,
    CAST(ItemCode AS VARCHAR)                   AS item_code,
    CAST(Status AS VARCHAR)                     AS status,
    CAST(Type AS VARCHAR)                       AS type,
    CAST(PlannedQty AS DECIMAL(19,6))           AS planned_qty,
    CAST(CmpltQty AS DECIMAL(19,6))             AS cmplt_qty,
    CAST(RjctQty AS DECIMAL(19,6))              AS rjct_qty,
    CAST(PostDate AS TIMESTAMP)                 AS post_date,
    CAST(DueDate AS TIMESTAMP)                  AS due_date,
    CAST(StartDate AS TIMESTAMP)                AS start_date,
    CAST(CloseDate AS TIMESTAMP)                AS close_date,
    CAST(Warehouse AS VARCHAR)                  AS warehouse,
    CAST(CreateDate AS TIMESTAMP)               AS create_date,
    CAST(CreateTS AS BIGINT)                    AS create_ts,
    CAST(UpdateDate AS TIMESTAMP)               AS update_date,
    CAST(UpdateTS AS BIGINT)                    AS update_ts,
    _company                              AS company,
    _source_updated_at                    AS source_updated_at,
    load_date
FROM latest
ORDER BY company, doc_entry
