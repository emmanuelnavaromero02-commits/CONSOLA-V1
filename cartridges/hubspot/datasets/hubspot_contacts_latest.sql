-- hubspot_contacts_latest  (silver)  cartridge: hubspot
-- sources: ["raw/hubspot/contacts"]
-- description: Última foto de cada contacto (dedup por hubspot_id, máxima lastmodifieddate). Limpieza 1:1 desde Bronze.

SELECT * EXCLUDE (_rn)
FROM (
    SELECT *,
        ROW_NUMBER() OVER (
            PARTITION BY hubspot_id
            ORDER BY load_date DESC, lastmodifieddate DESC
        ) AS _rn
    FROM read_parquet(
        's3://{bucket}/raw/hubspot/contacts/**/*.parquet',
        hive_partitioning = true,
        union_by_name = true
    )
)
WHERE _rn = 1
