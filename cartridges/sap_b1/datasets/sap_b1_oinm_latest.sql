-- sap_b1_oinm_latest  (silver)  cartridge: sap_b1
-- sources: ["raw/sap_b1/OINM"]
-- description: Inventory transaction log (a view over OIVL/IVL1 in B1 >= 8.8); rows never change. One TransNum per document with one row per line (TransSeq): TransNum is the watermark, (TransNum, TransSeq) the page key.

WITH latest AS (
    SELECT *
    FROM (
        SELECT *,
               ROW_NUMBER() OVER (
                   PARTITION BY _company, TransNum, TransSeq
                   ORDER BY load_date DESC, _extracted_at DESC
               ) AS _rn
        FROM read_parquet('s3://{bucket}/raw/sap_b1/OINM/**/*.parquet',
                      hive_partitioning = true,
                      union_by_name   = true)
    )
    WHERE _rn = 1
)
SELECT
    CAST(TransNum AS BIGINT)                    AS trans_num,
    CAST(TransSeq AS BIGINT)                    AS trans_seq,
    CAST(DocDate AS TIMESTAMP)                  AS doc_date,
    CAST(ItemCode AS VARCHAR)                   AS item_code,
    CAST(Warehouse AS VARCHAR)                  AS warehouse,
    CAST(InQty AS DECIMAL(19,6))                AS in_qty,
    CAST(OutQty AS DECIMAL(19,6))               AS out_qty,
    CAST(Price AS DECIMAL(19,6))                AS price,
    CAST(TransType AS BIGINT)                   AS trans_type,
    CAST(CreatedBy AS BIGINT)                   AS created_by,
    CAST(BASE_REF AS VARCHAR)                   AS base_ref,
    CAST(DocLineNum AS BIGINT)                  AS doc_line_num,
    CAST(Currency AS VARCHAR)                   AS currency,
    CAST(TransValue AS DECIMAL(19,6))           AS trans_value,
    CAST(CalcPrice AS DECIMAL(19,6))            AS calc_price,
    CAST(ApplObj AS VARCHAR)                    AS appl_obj,
    CAST(AppObjAbs AS BIGINT)                   AS app_obj_abs,
    CAST(CreateDate AS TIMESTAMP)               AS create_date,
    _company                              AS company,
    _source_updated_at                    AS source_updated_at,
    load_date
FROM latest
ORDER BY company, trans_num, trans_seq
