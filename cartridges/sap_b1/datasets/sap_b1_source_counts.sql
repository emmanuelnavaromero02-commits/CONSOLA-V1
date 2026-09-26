-- sap_b1_source_counts  (silver)  cartridge: sap_b1
-- sources: ["raw/sap_b1/SourceCounts"]
-- description: The newest row count per company and table that the agent took at the source database: the window it counted (24 months back for dated tables, the whole table otherwise), the rows, when it counted and the error when a count failed.

WITH counts AS (
    SELECT *, regexp_replace(_run_id, '-b[0-9]+$', '') AS _run_key
    FROM read_parquet('s3://{bucket}/raw/sap_b1/SourceCounts/**/*.parquet',
                      hive_partitioning = true,
                      union_by_name   = true)
),
newest AS (
    SELECT _company, entity, arg_max(_run_key, counted_at) AS _run_key
    FROM counts
    GROUP BY 1, 2
)
SELECT
    CAST(c._company AS VARCHAR)            AS company,
    CAST(c.entity AS VARCHAR)              AS entity,
    CAST(c.window_start AS TIMESTAMP)      AS window_start,
    CAST(c.window_end AS TIMESTAMP)        AS window_end,
    CAST(c.source_rows AS BIGINT)          AS source_rows,
    CAST(c.counted_at AS TIMESTAMP)        AS counted_at,
    CAST(c.error AS VARCHAR)               AS error,
    c._run_key                             AS count_id
FROM counts c
JOIN newest n ON n._company = c._company AND n.entity = c.entity AND n._run_key = c._run_key
ORDER BY company, entity
