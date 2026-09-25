-- sap_b1_stock_on_hand  (silver)  cartridge: sap_b1
-- sources: ["raw/sap_b1/OITW", "raw/sap_b1/OITM", "raw/sap_b1/OWHS", "raw/sap_b1/OADM"]
-- description: Stock per company, item and warehouse from the newest OITW snapshot: on hand, committed, on order, available, and the stock value at the item's average price in local currency.

WITH snapshot AS (
    SELECT * EXCLUDE (_rn)
    FROM (
        SELECT s.*, ROW_NUMBER() OVER (PARTITION BY s._company, s.ItemCode, s.WhsCode ORDER BY s._extracted_at DESC) AS _rn
        FROM read_parquet('s3://{bucket}/raw/sap_b1/OITW/**/*.parquet',
                          hive_partitioning = true,
                          union_by_name   = true) s
        JOIN (
            SELECT _company, arg_max(regexp_replace(_run_id, '-b[0-9]+$', ''), _extracted_at) AS _run_key
            FROM read_parquet('s3://{bucket}/raw/sap_b1/OITW/**/*.parquet',
                          hive_partitioning = true,
                          union_by_name   = true)
            GROUP BY _company
        ) n ON n._company = s._company AND regexp_replace(s._run_id, '-b[0-9]+$', '') = n._run_key
    )
    WHERE _rn = 1
),
items AS (
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
warehouses AS (
    SELECT * EXCLUDE (_rn)
    FROM (
        SELECT s.*, ROW_NUMBER() OVER (PARTITION BY s._company, s.WhsCode ORDER BY s._extracted_at DESC) AS _rn
        FROM read_parquet('s3://{bucket}/raw/sap_b1/OWHS/**/*.parquet',
                          hive_partitioning = true,
                          union_by_name   = true) s
        JOIN (
            SELECT _company, arg_max(regexp_replace(_run_id, '-b[0-9]+$', ''), _extracted_at) AS _run_key
            FROM read_parquet('s3://{bucket}/raw/sap_b1/OWHS/**/*.parquet',
                          hive_partitioning = true,
                          union_by_name   = true)
            GROUP BY _company
        ) n ON n._company = s._company AND regexp_replace(s._run_id, '-b[0-9]+$', '') = n._run_key
    )
    WHERE _rn = 1
),
company AS (
    SELECT _company, MainCurncy AS local_currency, SysCurrncy AS sys_currency
    FROM (
        SELECT s.*, ROW_NUMBER() OVER (PARTITION BY s._company, s.Code ORDER BY s._extracted_at DESC) AS _rn
        FROM read_parquet('s3://{bucket}/raw/sap_b1/OADM/**/*.parquet',
                          hive_partitioning = true,
                          union_by_name   = true) s
        JOIN (
            SELECT _company, arg_max(regexp_replace(_run_id, '-b[0-9]+$', ''), _extracted_at) AS _run_key
            FROM read_parquet('s3://{bucket}/raw/sap_b1/OADM/**/*.parquet',
                          hive_partitioning = true,
                          union_by_name   = true)
            GROUP BY _company
        ) n ON n._company = s._company AND regexp_replace(s._run_id, '-b[0-9]+$', '') = n._run_key
    )
    WHERE _rn = 1
)
SELECT
    s._company                                          AS company,
    CAST(s.ItemCode AS VARCHAR)                         AS item_code,
    CAST(i.ItemName AS VARCHAR)                         AS item_name,
    CAST(i.ItmsGrpCod AS BIGINT)                        AS item_group_code,
    CAST(i.ManBtchNum AS VARCHAR) = 'Y'                 AS batch_managed,
    CAST(s.WhsCode AS VARCHAR)                          AS warehouse,
    CAST(w.WhsName AS VARCHAR)                          AS warehouse_name,
    CAST(s.OnHand AS DECIMAL(19,6))                     AS on_hand,
    CAST(s.IsCommited AS DECIMAL(19,6))                 AS committed,
    CAST(s.OnOrder AS DECIMAL(19,6))                    AS on_order,
    CAST(s.OnHand - COALESCE(s.IsCommited, 0) AS DECIMAL(19,6)) AS available,
    CAST(s.AvgPrice AS DECIMAL(19,6))                   AS avg_price,
    CAST(s.OnHand * COALESCE(s.AvgPrice, 0) AS DECIMAL(19,6)) AS stock_value_local,
    c.local_currency                                    AS local_currency,
    c.sys_currency                                      AS sys_currency,
    CAST(s.MinStock AS DECIMAL(19,6))                   AS min_stock,
    CAST(s.MaxStock AS DECIMAL(19,6))                   AS max_stock,
    s.load_date
FROM snapshot s
LEFT JOIN items i ON i._company = s._company AND i.ItemCode = s.ItemCode
LEFT JOIN warehouses w ON w._company = s._company AND w.WhsCode = s.WhsCode
LEFT JOIN company c ON c._company = s._company
ORDER BY company, item_code, warehouse
