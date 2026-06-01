-- salesforce_deals_en_riesgo  (gold)  cartridge: salesforce
-- sources: ["silver/salesforce/salesforce_opportunity_latest", "silver/salesforce/salesforce_task_latest", "silver/salesforce/salesforce_event_latest", "silver/salesforce/salesforce_user_latest"]
-- description: Oportunidades abiertas en riesgo: sin actividad reciente o con cierre vencido.
WITH opp AS (
    SELECT * FROM read_parquet('s3://{bucket}/silver/salesforce/salesforce_opportunity_latest/**/*.parquet',
                               hive_partitioning = true, union_by_name = true)
    WHERE NOT is_closed
),
act AS (
    SELECT what_id AS opportunity_id, MAX(activity_date) AS last_activity FROM (
        SELECT what_id, activity_date FROM read_parquet('s3://{bucket}/silver/salesforce/salesforce_task_latest/**/*.parquet',
                               hive_partitioning = true, union_by_name = true)
        UNION ALL
        SELECT what_id, activity_date FROM read_parquet('s3://{bucket}/silver/salesforce/salesforce_event_latest/**/*.parquet',
                               hive_partitioning = true, union_by_name = true)
    ) a
    WHERE what_id IS NOT NULL
    GROUP BY what_id
),
usr AS (
    SELECT user_id, user_name FROM read_parquet('s3://{bucket}/silver/salesforce/salesforce_user_latest/**/*.parquet',
                               hive_partitioning = true, union_by_name = true)
)
SELECT
    o.opportunity_id,
    o.opportunity_name,
    COALESCE(u.user_name, '(sin dueño)')                          AS vendedor,
    o.stage_name,
    ROUND(o.amount, 2)                                            AS amount,
    o.close_date,
    a.last_activity,
    DATE_DIFF('day', a.last_activity, CURRENT_DATE)               AS dias_sin_actividad,
    CASE
        WHEN o.close_date < CURRENT_DATE THEN 'cierre vencido'
        WHEN a.last_activity IS NULL THEN 'nunca hubo actividad'
        WHEN DATE_DIFF('day', a.last_activity, CURRENT_DATE) > 14 THEN 'sin actividad reciente'
        ELSE 'ok'
    END                                                          AS motivo_riesgo
FROM opp o
LEFT JOIN act a ON o.opportunity_id = a.opportunity_id
LEFT JOIN usr u ON o.owner_id = u.user_id
WHERE o.close_date < CURRENT_DATE
   OR a.last_activity IS NULL
   OR DATE_DIFF('day', a.last_activity, CURRENT_DATE) > 14
ORDER BY o.amount DESC NULLS LAST
