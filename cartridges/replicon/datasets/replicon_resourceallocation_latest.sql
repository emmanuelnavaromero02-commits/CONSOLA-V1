-- replicon_resourceallocation_latest  (silver)  cartridge: replicon
-- sources: ["raw/replicon/ResourceAllocation"]
-- description: Asignación de recursos

SELECT date, userid, username, projectid, projectcode, projectname, durationhours,
    COALESCE(
        CASE WHEN CAST(userbillingratebasecurrency AS DOUBLE) > 0 THEN CAST(userbillingratebasecurrency AS DOUBLE) END,
        CASE WHEN CAST(projectrolebillingratebasecurrency AS DOUBLE) > 0 THEN CAST(projectrolebillingratebasecurrency AS DOUBLE) END,
        0.0
    ) AS billing_rate_usd,
    COALESCE(CAST(usercostratebasecurrency AS DOUBLE), 0.0) AS cost_rate_usd,
    load_date
FROM (
    SELECT *, ROW_NUMBER() OVER (PARTITION BY date, userid, projectid ORDER BY load_date DESC NULLS LAST) AS rn
    FROM read_parquet(
        's3://{bucket}/raw/replicon/ResourceAllocation/load_date=*/data.parquet',
        hive_partitioning=true, union_by_name=true)
    WHERE load_date = '{latest_date}'
) t
WHERE rn = 1 AND projectcode IS NOT NULL AND userid IS NOT NULL
