-- hubspot_companies_latest  (silver)  cartridge: hubspot
-- sources: ["raw/hubspot/companies"]
-- description: Última foto de cada empresa/cuenta (dedup por hubspot_id, máxima hs_lastmodifieddate). Limpieza 1:1 desde Bronze.

SELECT * EXCLUDE (_rn)
FROM (
    SELECT *,
        ROW_NUMBER() OVER (
            PARTITION BY hubspot_id
            ORDER BY load_date DESC, hs_lastmodifieddate DESC
        ) AS _rn
    FROM read_parquet(
        's3://{bucket}/raw/hubspot/companies/**/*.parquet',
        hive_partitioning = true,
        union_by_name = true
    )
)
WHERE _rn = 1
