-- sap_b1_ordn_latest  (silver)  cartridge: sap_b1
-- sources: ["raw/sap_b1/ORDN"]
-- description: Returns headers, ObjType 16: totals in document, local and system currency; CANCELED N/Y/C.

WITH latest AS (
    SELECT *
    FROM (
        SELECT *,
               ROW_NUMBER() OVER (
                   PARTITION BY _company, DocEntry
                   ORDER BY _source_updated_at DESC NULLS LAST, load_date DESC, _extracted_at DESC
               ) AS _rn
        FROM read_parquet('s3://{bucket}/raw/sap_b1/ORDN/**/*.parquet',
                      hive_partitioning = true,
                      union_by_name   = true)
    )
    WHERE _rn = 1
)
SELECT
    CAST(DocEntry AS BIGINT)                    AS doc_entry,
    CAST(DocNum AS BIGINT)                      AS doc_num,
    CAST(DocType AS VARCHAR)                    AS doc_type,
    CAST(CANCELED AS VARCHAR)                   AS canceled,
    CAST(DocStatus AS VARCHAR)                  AS doc_status,
    CAST(ObjType AS VARCHAR)                    AS obj_type,
    CAST(DocDate AS TIMESTAMP)                  AS doc_date,
    CAST(DocDueDate AS TIMESTAMP)               AS doc_due_date,
    CAST(TaxDate AS TIMESTAMP)                  AS tax_date,
    CAST(CardCode AS VARCHAR)                   AS card_code,
    CAST(CardName AS VARCHAR)                   AS card_name,
    CAST(NumAtCard AS VARCHAR)                  AS num_at_card,
    CAST(DocCur AS VARCHAR)                     AS doc_cur,
    CAST(DocRate AS DECIMAL(19,6))              AS doc_rate,
    CAST(DocTotal AS DECIMAL(19,6))             AS doc_total,
    CAST(DocTotalFC AS DECIMAL(19,6))           AS doc_total_fc,
    CAST(DocTotalSy AS DECIMAL(19,6))           AS doc_total_sy,
    CAST(VatSum AS DECIMAL(19,6))               AS vat_sum,
    CAST(VatSumFC AS DECIMAL(19,6))             AS vat_sum_fc,
    CAST(VatSumSy AS DECIMAL(19,6))             AS vat_sum_sy,
    CAST(DiscSum AS DECIMAL(19,6))              AS disc_sum,
    CAST(GrosProfit AS DECIMAL(19,6))           AS gros_profit,
    CAST(GrosProfFC AS DECIMAL(19,6))           AS gros_prof_fc,
    CAST(GrosProfSy AS DECIMAL(19,6))           AS gros_prof_sy,
    CAST(SlpCode AS BIGINT)                     AS slp_code,
    CAST(GroupNum AS BIGINT)                    AS group_num,
    CAST(Comments AS VARCHAR)                   AS comments,
    CAST(TransId AS BIGINT)                     AS trans_id,
    CAST(BPLId AS BIGINT)                       AS bpl_id,
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
