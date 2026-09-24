-- sap_b1_items  (silver)  cartridge: sap_b1
-- sources: ["raw/sap_b1/OITM", "raw/sap_b1/OITB"]
-- description: Current item master per company with its item group, the inventory/sales/purchase flags, batch management and the default warehouse.

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
groups AS (
    -- Newest run per company (all of its batches), never a mix of two runs.
    SELECT * EXCLUDE (_rn)
    FROM (
        SELECT s.*, ROW_NUMBER() OVER (PARTITION BY s._company, s.ItmsGrpCod ORDER BY s._extracted_at DESC) AS _rn
        FROM read_parquet('s3://{bucket}/raw/sap_b1/OITB/**/*.parquet',
                          hive_partitioning = true,
                          union_by_name   = true) s
        JOIN (
            SELECT _company, arg_max(regexp_replace(_run_id, '-b[0-9]+$', ''), _extracted_at) AS _run_key
            FROM read_parquet('s3://{bucket}/raw/sap_b1/OITB/**/*.parquet',
                          hive_partitioning = true,
                          union_by_name   = true)
            GROUP BY _company
        ) n ON n._company = s._company AND regexp_replace(s._run_id, '-b[0-9]+$', '') = n._run_key
    )
    WHERE _rn = 1
)
SELECT
    i._company                              AS company,
    CAST(i.ItemCode AS VARCHAR)             AS item_code,
    CAST(i.ItemName AS VARCHAR)             AS item_name,
    CAST(i.ItmsGrpCod AS BIGINT)            AS item_group_code,
    CAST(g.ItmsGrpNam AS VARCHAR)           AS item_group_name,
    CAST(i.InvntItem AS VARCHAR) = 'Y'      AS inventory_item,
    CAST(i.SellItem AS VARCHAR) = 'Y'       AS sell_item,
    CAST(i.PrchseItem AS VARCHAR) = 'Y'     AS purchase_item,
    CAST(i.ManBtchNum AS VARCHAR) = 'Y'     AS batch_managed,
    CAST(i.DfltWH AS VARCHAR)               AS default_warehouse,
    CAST(i.AvgPrice AS DECIMAL(19,6))       AS avg_price,
    CAST(i.LastPurPrc AS DECIMAL(19,6))     AS last_purchase_price,
    CAST(i.validFor AS VARCHAR)             AS valid_for,
    CAST(i.frozenFor AS VARCHAR)            AS frozen_for,
    CAST(i.CreateDate AS TIMESTAMP)         AS created_at,
    i._source_updated_at                    AS source_updated_at,
    i.load_date
FROM oitm i
LEFT JOIN groups g ON g._company = i._company AND g.ItmsGrpCod = i.ItmsGrpCod
ORDER BY company, item_code
