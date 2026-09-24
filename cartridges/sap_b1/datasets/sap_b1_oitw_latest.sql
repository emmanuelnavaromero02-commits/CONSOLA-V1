-- sap_b1_oitw_latest  (silver)  cartridge: sap_b1
-- sources: ["raw/sap_b1/OITW"]
-- description: On-hand, committed and on-order quantities per item and warehouse (snapshot).

WITH newest_run AS (
    -- Instantánea sin marca de agua: la corrida más reciente POR EMPRESA
    -- (todos sus lotes) y nada más. Deduplicar el histórico resucitaría
    -- filas que la fuente borró; quedarse con MAX(load_date) mezclaría dos
    -- corridas del mismo día.
    SELECT _company,
           arg_max(regexp_replace(_run_id, '-b[0-9]+$', ''), _extracted_at) AS _run_key
    FROM read_parquet('s3://{bucket}/raw/sap_b1/OITW/**/*.parquet',
                      hive_partitioning = true,
                      union_by_name   = true)
    GROUP BY _company
),
latest AS (
    SELECT *
    FROM (
        SELECT s.*,
               ROW_NUMBER() OVER (PARTITION BY s._company, s.ItemCode, s.WhsCode ORDER BY s._extracted_at DESC) AS _rn
        FROM read_parquet('s3://{bucket}/raw/sap_b1/OITW/**/*.parquet',
                      hive_partitioning = true,
                      union_by_name   = true) s
        JOIN newest_run n
          ON n._company = s._company
         AND regexp_replace(s._run_id, '-b[0-9]+$', '') = n._run_key
    )
    WHERE _rn = 1
)
SELECT
    CAST(ItemCode AS VARCHAR)                   AS item_code,
    CAST(WhsCode AS VARCHAR)                    AS whs_code,
    CAST(OnHand AS DECIMAL(19,6))               AS on_hand,
    CAST(IsCommited AS DECIMAL(19,6))           AS is_commited,
    CAST(OnOrder AS DECIMAL(19,6))              AS on_order,
    CAST(AvgPrice AS DECIMAL(19,6))             AS avg_price,
    CAST(MinStock AS DECIMAL(19,6))             AS min_stock,
    CAST(MaxStock AS DECIMAL(19,6))             AS max_stock,
    _company                              AS company,
    load_date
FROM latest
ORDER BY company, item_code, whs_code
