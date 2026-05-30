-- hubspot_owners_latest  (silver)  cartridge: hubspot
-- sources: ["raw/hubspot/owners"]
-- description: Última foto de cada vendedor/dueño (dedup por hubspot_id, máxima updated_at). Limpieza 1:1 desde Bronze.

SELECT * EXCLUDE (_rn)
FROM (
    SELECT *,
        ROW_NUMBER() OVER (
            PARTITION BY hubspot_id
            ORDER BY load_date DESC, updated_at DESC
        ) AS _rn
    FROM read_parquet(
        's3://{bucket}/raw/hubspot/owners/**/*.parquet',
        hive_partitioning = true,
        union_by_name = true
    )
)
WHERE _rn = 1
