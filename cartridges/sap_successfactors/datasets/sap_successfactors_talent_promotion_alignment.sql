-- sap_successfactors_talent_promotion_alignment  (gold)  cartridge: sap_successfactors
-- sources: ["gold/sap_successfactors/sap_successfactors_talent_9box", "gold/sap_successfactors/sap_successfactors_talent_mobility_history"]
-- description: Alineacion de promociones contra 9-box usando eventReason observado. Si no hay C/P/A o promociones, queda parcial.

WITH nine_box AS (
    SELECT user_id, box_key, box_label, box_status
    FROM read_parquet('s3://{bucket}/gold/sap_successfactors/sap_successfactors_talent_9box/**/*.parquet',
                      hive_partitioning = true,
                      union_by_name = true)
),
mobility AS (
    SELECT user_id, latest_event_reason
    FROM read_parquet('s3://{bucket}/gold/sap_successfactors/sap_successfactors_talent_mobility_history/**/*.parquet',
                      hive_partitioning = true,
                      union_by_name = true)
),
promotions AS (
    SELECT
        nine_box.box_key,
        nine_box.box_label,
        nine_box.box_status,
        COUNT(*) AS promotion_count,
        COUNT(*) FILTER (WHERE nine_box.box_key IN ('estrella', 'crecimiento', 'alto_impacto')) AS aligned_count,
        COUNT(*) FILTER (WHERE nine_box.box_key NOT IN ('estrella', 'crecimiento', 'alto_impacto')) AS misaligned_count
    FROM mobility
    JOIN nine_box ON nine_box.user_id = mobility.user_id
    WHERE LOWER(COALESCE(mobility.latest_event_reason, '')) LIKE '%promot%'
       OR LOWER(COALESCE(mobility.latest_event_reason, '')) LIKE '%promotion%'
       OR LOWER(COALESCE(mobility.latest_event_reason, '')) LIKE '%promo%'
    GROUP BY nine_box.box_key, nine_box.box_label, nine_box.box_status
)
SELECT
    COALESCE(box_key, 'no_promotions_observed') AS box_key,
    COALESCE(box_label, 'Sin promociones observadas') AS box_label,
    COALESCE(promotion_count, 0) AS promotion_count,
    COALESCE(aligned_count, 0) AS aligned_count,
    COALESCE(misaligned_count, 0) AS misaligned_count,
    CASE
        WHEN COALESCE(promotion_count, 0) = 0 THEN 'partial'
        WHEN box_status = 'ready' THEN 'ready'
        ELSE 'blocked'
    END AS status,
    CURRENT_TIMESTAMP AS generated_at
FROM promotions
UNION ALL
SELECT
    'summary' AS box_key,
    'Promociones vs calibracion' AS box_label,
    COALESCE(SUM(promotion_count), 0) AS promotion_count,
    COALESCE(SUM(aligned_count), 0) AS aligned_count,
    COALESCE(SUM(misaligned_count), 0) AS misaligned_count,
    CASE WHEN COALESCE(SUM(promotion_count), 0) = 0 THEN 'partial' ELSE 'ready' END AS status,
    CURRENT_TIMESTAMP AS generated_at
FROM promotions
