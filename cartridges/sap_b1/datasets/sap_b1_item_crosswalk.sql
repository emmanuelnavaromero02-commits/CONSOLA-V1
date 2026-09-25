-- sap_b1_item_crosswalk  (silver)  cartridge: sap_b1
-- sources: ["raw/sap_b1/OITM"]
-- description: One item identity across the companies: keyed by its barcode (EAN/UPC, 8 to 14 digits) when present, otherwise by the item code, which Business One groups usually share; companies_sharing_key counts the companies that carry the same item.

WITH oitm AS (
    SELECT * EXCLUDE (_rn)
    FROM (
        SELECT *,
               ROW_NUMBER() OVER (
                   PARTITION BY _company, ItemCode
                   ORDER BY _source_updated_at DESC NULLS LAST, load_date DESC, _extracted_at DESC
               ) AS _rn
        FROM read_parquet('s3://{bucket}/raw/sap_b1/OITM/**/*.parquet',
                          hive_partitioning = true,
                          union_by_name   = true)
    )
    WHERE _rn = 1
),
items AS (
    SELECT
        _company                                                          AS company,
        CAST(ItemCode AS VARCHAR)                                         AS item_code,
        CAST(ItemName AS VARCHAR)                                         AS item_name,
        NULLIF(regexp_replace(COALESCE(CAST(CodeBars AS VARCHAR), ''), '[^0-9]', '', 'g'), '') AS barcode_digits,
        CAST(SuppCatNum AS VARCHAR)                                       AS supplier_catalog_number,
        CAST(PrcrmntMtd AS VARCHAR)                                       AS procurement_method
    FROM oitm
),
keyed AS (
    SELECT *,
           CASE WHEN length(barcode_digits) BETWEEN 8 AND 14 THEN barcode_digits END AS barcode
    FROM items
)
SELECT
    company,
    item_code,
    item_name,
    barcode,
    supplier_catalog_number,
    procurement_method,
    CASE WHEN barcode IS NOT NULL THEN 'EAN:' || barcode ELSE 'CODE:' || item_code END AS item_key,
    CASE WHEN barcode IS NOT NULL THEN 'barcode' ELSE 'code' END                      AS match_method,
    COUNT(DISTINCT company) OVER (
        PARTITION BY CASE WHEN barcode IS NOT NULL THEN 'EAN:' || barcode ELSE 'CODE:' || item_code END
    )                                                                                 AS companies_sharing_key
FROM keyed
ORDER BY company, item_code
