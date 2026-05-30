-- hubspot_pipelines_latest  (silver)  cartridge: hubspot
-- sources: ["raw/hubspot/pipelines"]
-- description: Etapas de pipeline de deals con su probabilidad (lookup etapa→probabilidad). Dedup por (pipeline_id, stage_id), última carga.

SELECT * EXCLUDE (_rn)
FROM (
    SELECT *,
        ROW_NUMBER() OVER (
            PARTITION BY pipeline_id, stage_id
            ORDER BY load_date DESC
        ) AS _rn
    FROM read_parquet(
        's3://{bucket}/raw/hubspot/pipelines/**/*.parquet',
        hive_partitioning = true,
        union_by_name = true
    )
)
WHERE _rn = 1
