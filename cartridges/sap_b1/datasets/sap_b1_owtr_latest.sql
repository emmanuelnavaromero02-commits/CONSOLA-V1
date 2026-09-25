-- sap_b1_owtr_latest  (silver)  cartridge: sap_b1
-- sources: ["raw/sap_b1/OWTR"]
-- description: Inventory transfer headers, ObjType 67: source warehouse (Filler) and target warehouse (ToWhsCode); stock moves, money does not.

WITH latest AS (
    SELECT *
    FROM (
        SELECT *,
               ROW_NUMBER() OVER (
                   PARTITION BY _company, DocEntry
                   ORDER BY _source_updated_at DESC NULLS LAST, load_date DESC, _extracted_at DESC
               ) AS _rn
        FROM read_parquet('s3://{bucket}/raw/sap_b1/OWTR/**/*.parquet',
                      hive_partitioning = true,
                      union_by_name   = true)
    )
    WHERE _rn = 1
)
SELECT
    CAST(DocEntry AS BIGINT)                    AS doc_entry,
    CAST(DocNum AS BIGINT)                      AS doc_num,
    CAST(CANCELED AS VARCHAR)                   AS canceled,
    CAST(DocStatus AS VARCHAR)                  AS doc_status,
    CAST(ObjType AS VARCHAR)                    AS obj_type,
    CAST(DocDate AS TIMESTAMP)                  AS doc_date,
    CAST(TaxDate AS TIMESTAMP)                  AS tax_date,
    CAST(CardCode AS VARCHAR)                   AS card_code,
    CAST(CardName AS VARCHAR)                   AS card_name,
    CAST(Filler AS VARCHAR)                     AS filler,
    CAST(ToWhsCode AS VARCHAR)                  AS to_whs_code,
    CAST(Comments AS VARCHAR)                   AS comments,
    CAST(TransId AS BIGINT)                     AS trans_id,
    CAST(Series AS BIGINT)                      AS series,
    CAST(CreateDate AS TIMESTAMP)               AS create_date,
    CAST(CreateTS AS BIGINT)                    AS create_ts,
    CAST(UpdateDate AS TIMESTAMP)               AS update_date,
    CAST(UpdateTS AS BIGINT)                    AS update_ts,
    CAST(UserSign AS BIGINT)                    AS user_sign,
    _company                              AS company,
    _source_updated_at                    AS source_updated_at,
    load_date
FROM latest
ORDER BY company, doc_entry
