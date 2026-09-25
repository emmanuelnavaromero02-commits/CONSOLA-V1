-- sap_b1_ocrd_latest  (silver)  cartridge: sap_b1
-- sources: ["raw/sap_b1/OCRD"]
-- description: Customers (CardType C) and suppliers (CardType S) with their currency and group.

WITH latest AS (
    SELECT *
    FROM (
        SELECT *,
               ROW_NUMBER() OVER (
                   PARTITION BY _company, CardCode
                   ORDER BY _source_updated_at DESC NULLS LAST, load_date DESC, _extracted_at DESC
               ) AS _rn
        FROM read_parquet('s3://{bucket}/raw/sap_b1/OCRD/**/*.parquet',
                      hive_partitioning = true,
                      union_by_name   = true)
    )
    WHERE _rn = 1
)
SELECT
    CAST(CardCode AS VARCHAR)                   AS card_code,
    CAST(CardName AS VARCHAR)                   AS card_name,
    CAST(CardType AS VARCHAR)                   AS card_type,
    CAST(GroupCode AS BIGINT)                   AS group_code,
    CAST(Currency AS VARCHAR)                   AS currency,
    CAST(SlpCode AS BIGINT)                     AS slp_code,
    CAST(Country AS VARCHAR)                    AS country,
    CAST(LicTradNum AS VARCHAR)                 AS lic_trad_num,
    CAST(validFor AS VARCHAR)                   AS valid_for,
    CAST(frozenFor AS VARCHAR)                  AS frozen_for,
    CAST(CreateDate AS TIMESTAMP)               AS create_date,
    CAST(UpdateDate AS TIMESTAMP)               AS update_date,
    CAST(UpdateTS AS BIGINT)                    AS update_ts,
    _company                              AS company,
    _source_updated_at                    AS source_updated_at,
    load_date
FROM latest
ORDER BY company, card_code
