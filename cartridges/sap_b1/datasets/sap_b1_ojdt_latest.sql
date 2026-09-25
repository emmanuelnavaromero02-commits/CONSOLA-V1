-- sap_b1_ojdt_latest  (silver)  cartridge: sap_b1
-- sources: ["raw/sap_b1/OJDT"]
-- description: Journal entry headers: RefDate, TransType (originating object), StornoToTr for reversals.

WITH latest AS (
    SELECT *
    FROM (
        SELECT *,
               ROW_NUMBER() OVER (
                   PARTITION BY _company, TransId
                   ORDER BY _source_updated_at DESC NULLS LAST, load_date DESC, _extracted_at DESC
               ) AS _rn
        FROM read_parquet('s3://{bucket}/raw/sap_b1/OJDT/**/*.parquet',
                      hive_partitioning = true,
                      union_by_name   = true)
    )
    WHERE _rn = 1
)
SELECT
    CAST(TransId AS BIGINT)                     AS trans_id,
    CAST(Number AS BIGINT)                      AS number,
    CAST(RefDate AS TIMESTAMP)                  AS ref_date,
    CAST(DueDate AS TIMESTAMP)                  AS due_date,
    CAST(TaxDate AS TIMESTAMP)                  AS tax_date,
    CAST(Memo AS VARCHAR)                       AS memo,
    CAST(TransType AS VARCHAR)                  AS trans_type,
    CAST(BaseRef AS VARCHAR)                    AS base_ref,
    CAST(CreatedBy AS BIGINT)                   AS created_by,
    CAST(StornoToTr AS BIGINT)                  AS storno_to_tr,
    CAST(CreateDate AS TIMESTAMP)               AS create_date,
    CAST(CreateTS AS BIGINT)                    AS create_ts,
    CAST(UpdateDate AS TIMESTAMP)               AS update_date,
    CAST(UpdateTS AS BIGINT)                    AS update_ts,
    _company                              AS company,
    _source_updated_at                    AS source_updated_at,
    load_date
FROM latest
ORDER BY company, trans_id
