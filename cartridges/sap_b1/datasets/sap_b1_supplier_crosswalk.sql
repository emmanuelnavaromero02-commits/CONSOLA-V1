-- sap_b1_supplier_crosswalk  (silver)  cartridge: sap_b1
-- sources: ["raw/sap_b1/OCRD", "raw/sap_b1/IntercompanyPartners"]
-- description: One supplier identity across the companies: external suppliers (CardType S, not a group company) keyed by their normalized tax id (RFC) when it is valid and not the generic RFC, otherwise by company and code; companies_sharing_key counts the companies that buy from the same supplier.

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
partners AS (
    SELECT s._company, s.CardCode
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
),
suppliers AS (
    SELECT
        o._company                                                        AS company,
        CAST(o.CardCode AS VARCHAR)                                       AS card_code,
        CAST(o.CardName AS VARCHAR)                                       AS card_name,
        CAST(o.LicTradNum AS VARCHAR)                                     AS rfc_raw,
        NULLIF(regexp_replace(upper(COALESCE(CAST(o.LicTradNum AS VARCHAR), '')), '[^A-Z0-9Ñ&]', '', 'g'), '') AS rfc_norm
    FROM ocrd o
    LEFT JOIN partners p ON p._company = o._company AND p.CardCode = o.CardCode
    WHERE CAST(o.CardType AS VARCHAR) = 'S' AND p.CardCode IS NULL
),
classified AS (
    SELECT *,
           COALESCE(regexp_full_match(rfc_norm, '[A-ZÑ&]{3,4}[0-9]{6}[A-Z0-9]{3}'), FALSE) AS rfc_valid,
           COALESCE(rfc_norm IN ('XAXX010101000', 'XEXX010101000'), FALSE)              AS rfc_generic
    FROM suppliers
),
keyed AS (
    SELECT *,
           CASE WHEN rfc_valid AND NOT rfc_generic THEN 'RFC:' || rfc_norm
                ELSE company || ':' || card_code END                  AS supplier_key,
           CASE WHEN rfc_valid AND NOT rfc_generic THEN 'rfc'
                WHEN rfc_generic THEN 'generic_rfc'
                WHEN rfc_norm IS NULL THEN 'missing_rfc'
                ELSE 'invalid_rfc' END                                AS match_method
    FROM classified
)
SELECT
    company,
    card_code,
    card_name,
    rfc_raw,
    rfc_norm,
    rfc_valid,
    rfc_generic,
    supplier_key,
    match_method,
    COUNT(DISTINCT company) OVER (PARTITION BY supplier_key)          AS companies_sharing_key
FROM keyed
ORDER BY company, card_code
