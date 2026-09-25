-- sap_b1_business_partners  (silver)  cartridge: sap_b1
-- sources: ["raw/sap_b1/OCRD", "raw/sap_b1/OCRG", "raw/sap_b1/IntercompanyPartners"]
-- description: Current business partners per company (customers C, suppliers S) with their tax id (RFC), group name and the intercompany flag from the configured partner mapping.

WITH ocrd AS (
    SELECT * EXCLUDE (_rn)
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
),
groups AS (
    SELECT * EXCLUDE (_rn)
    FROM (
        SELECT s.*, ROW_NUMBER() OVER (PARTITION BY s._company, s.GroupCode ORDER BY s._extracted_at DESC) AS _rn
        FROM read_parquet('s3://{bucket}/raw/sap_b1/OCRG/**/*.parquet',
                          hive_partitioning = true,
                          union_by_name   = true) s
        JOIN (
            SELECT _company, arg_max(regexp_replace(_run_id, '-b[0-9]+$', ''), _extracted_at) AS _run_key
            FROM read_parquet('s3://{bucket}/raw/sap_b1/OCRG/**/*.parquet',
                          hive_partitioning = true,
                          union_by_name   = true)
            GROUP BY _company
        ) n ON n._company = s._company AND regexp_replace(s._run_id, '-b[0-9]+$', '') = n._run_key
    )
    WHERE _rn = 1
),
partners AS (
    SELECT _company, CardCode, CounterpartyCompany, MappingSource
    FROM (
        SELECT s.*, ROW_NUMBER() OVER (PARTITION BY s._company, s.CardCode ORDER BY s._extracted_at DESC) AS _rn
        FROM read_parquet('s3://{bucket}/raw/sap_b1/IntercompanyPartners/**/*.parquet',
                          hive_partitioning = true,
                          union_by_name   = true) s
        JOIN (
            SELECT _company, arg_max(regexp_replace(_run_id, '-b[0-9]+$', ''), _extracted_at) AS _run_key
            FROM read_parquet('s3://{bucket}/raw/sap_b1/IntercompanyPartners/**/*.parquet',
                          hive_partitioning = true,
                          union_by_name   = true)
            GROUP BY _company
        ) n ON n._company = s._company AND regexp_replace(s._run_id, '-b[0-9]+$', '') = n._run_key
    )
    WHERE _rn = 1
)
SELECT
    b._company                              AS company,
    CAST(b.CardCode AS VARCHAR)             AS card_code,
    CAST(b.CardName AS VARCHAR)             AS card_name,
    CAST(b.CardType AS VARCHAR)             AS card_type,
    CAST(b.GroupCode AS BIGINT)             AS group_code,
    CAST(g.GroupName AS VARCHAR)            AS group_name,
    CAST(b.Currency AS VARCHAR)             AS partner_currency,
    CAST(b.SlpCode AS BIGINT)               AS slp_code,
    CAST(b.Country AS VARCHAR)              AS country,
    CAST(b.LicTradNum AS VARCHAR)           AS rfc,
    CAST(b.validFor AS VARCHAR)             AS valid_for,
    CAST(b.frozenFor AS VARCHAR)            AS frozen_for,
    p.CardCode IS NOT NULL                  AS is_intercompany,
    CAST(p.CounterpartyCompany AS VARCHAR)  AS counterparty_company,
    CAST(p.MappingSource AS VARCHAR)        AS mapping_source,
    CAST(b.CreateDate AS TIMESTAMP)         AS created_at,
    b._source_updated_at                    AS source_updated_at,
    b.load_date
FROM ocrd b
LEFT JOIN groups g ON g._company = b._company AND g.GroupCode = b.GroupCode
LEFT JOIN partners p ON p._company = b._company AND p.CardCode = b.CardCode
ORDER BY company, card_code
