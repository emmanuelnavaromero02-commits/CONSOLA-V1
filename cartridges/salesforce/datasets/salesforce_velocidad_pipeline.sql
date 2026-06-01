-- salesforce_velocidad_pipeline  (gold)  cartridge: salesforce
-- sources: ["silver/salesforce/salesforce_opportunityhistory_latest"]
-- description: Días promedio que una oportunidad permanece en cada etapa (desde OpportunityHistory).
WITH h AS (
    SELECT
        opportunity_id,
        stage_name,
        created_at,
        LEAD(created_at) OVER (PARTITION BY opportunity_id ORDER BY created_at) AS next_change_at
    FROM read_parquet('s3://{bucket}/silver/salesforce/salesforce_opportunityhistory_latest/**/*.parquet',
                      hive_partitioning = true, union_by_name = true)
)
SELECT
    stage_name,
    COUNT(*)                                                        AS transiciones,
    ROUND(AVG(DATE_DIFF('day', created_at, next_change_at)), 1)     AS dias_promedio_en_etapa,
    MAX(DATE_DIFF('day', created_at, next_change_at))               AS dias_max_en_etapa
FROM h
WHERE next_change_at IS NOT NULL
GROUP BY stage_name
ORDER BY dias_promedio_en_etapa DESC
