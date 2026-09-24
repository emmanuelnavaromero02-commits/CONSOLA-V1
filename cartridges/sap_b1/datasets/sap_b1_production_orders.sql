-- sap_b1_production_orders  (silver)  cartridge: sap_b1
-- sources: ["raw/sap_b1/OWOR", "raw/sap_b1/WOR1"]
-- description: Production orders with their components (OWOR/WOR1): planned, completed and rejected quantities, dates and status; one row per component line.

WITH orders AS (
    SELECT * EXCLUDE (_rn)
    FROM (
        SELECT *,
               ROW_NUMBER() OVER (
                   PARTITION BY _company, DocEntry
                   ORDER BY _source_updated_at DESC NULLS LAST, load_date DESC, _extracted_at DESC
               ) AS _rn
        FROM read_parquet('s3://{bucket}/raw/sap_b1/OWOR/**/*.parquet',
                          hive_partitioning = true,
                          union_by_name   = true)
    )
    WHERE _rn = 1
),
component_versions AS (
    SELECT * EXCLUDE (_rn)
    FROM (
        SELECT *,
               ROW_NUMBER() OVER (
                   PARTITION BY _company, DocEntry, LineNum
                   ORDER BY _source_updated_at DESC NULLS LAST, load_date DESC, _extracted_at DESC
               ) AS _rn
        FROM read_parquet('s3://{bucket}/raw/sap_b1/WOR1/**/*.parquet',
                          hive_partitioning = true,
                          union_by_name   = true)
    )
    WHERE _rn = 1
),
components AS (
    SELECT * EXCLUDE (_order_stamp)
    FROM (
        SELECT *, MAX(_source_updated_at) OVER (PARTITION BY _company, DocEntry) AS _order_stamp
        FROM component_versions
    )
    WHERE _source_updated_at IS NULL OR _source_updated_at = _order_stamp
)
SELECT
    o._company                                          AS company,
    CAST(o.DocEntry AS BIGINT)                          AS doc_entry,
    CAST(o.DocNum AS BIGINT)                            AS doc_num,
    CAST(o.Status AS VARCHAR)                           AS status,
    CAST(o.Type AS VARCHAR)                             AS order_type,
    CAST(o.ItemCode AS VARCHAR)                         AS product_item_code,
    CAST(o.PlannedQty AS DECIMAL(19,6))                 AS planned_qty,
    CAST(o.CmpltQty AS DECIMAL(19,6))                   AS completed_qty,
    CAST(o.RjctQty AS DECIMAL(19,6))                    AS rejected_qty,
    CAST(o.PostDate AS TIMESTAMP)                       AS posting_date,
    CAST(DATE_TRUNC('month', CAST(o.PostDate AS TIMESTAMP)) AS DATE) AS doc_month,
    CAST(o.DueDate AS TIMESTAMP)                        AS due_date,
    CAST(o.StartDate AS TIMESTAMP)                      AS start_date,
    CAST(o.CloseDate AS TIMESTAMP)                      AS close_date,
    CAST(o.Warehouse AS VARCHAR)                        AS warehouse,
    CAST(c.LineNum AS BIGINT)                           AS component_line,
    CAST(c.ItemCode AS VARCHAR)                         AS component_item_code,
    CAST(c.BaseQty AS DECIMAL(19,6))                    AS component_base_qty,
    CAST(c.PlannedQty AS DECIMAL(19,6))                 AS component_planned_qty,
    CAST(c.IssuedQty AS DECIMAL(19,6))                  AS component_issued_qty,
    CAST(c.wareHouse AS VARCHAR)                        AS component_warehouse,
    o._source_updated_at                                AS source_updated_at,
    o.load_date
FROM orders o
LEFT JOIN components c ON c._company = o._company AND c.DocEntry = o.DocEntry
ORDER BY company, doc_entry, component_line
