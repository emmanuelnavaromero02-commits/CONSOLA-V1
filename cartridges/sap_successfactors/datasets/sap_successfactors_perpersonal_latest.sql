-- sap_successfactors_perpersonal_latest  (silver)  cartridge: sap_successfactors
-- sources: ["raw/sap_successfactors/PerPersonal"]
-- description: Última extracción de PerPersonal (datos personales efectivo-fechados). Nombre masked desde bronze; personIdExternal en claro (casa con Emp*).

-- NOTA: PerPersonal no declara select_fields; campos SF estándar.
WITH latest AS (
    SELECT *
    FROM read_parquet('s3://{bucket}/raw/sap_successfactors/PerPersonal/**/*.parquet',
                      hive_partitioning = true,
                      union_by_name   = true)
    WHERE load_date = (SELECT MAX(load_date)
                       FROM read_parquet('s3://{bucket}/raw/sap_successfactors/PerPersonal/**/*.parquet',
                                          hive_partitioning = true))
)
SELECT
    personIdExternal     AS person_id_external,   -- plano (sin regla de protección)
    firstName            AS first_name,           -- masked en bronze
    lastName             AS last_name,            -- masked en bronze
    gender               AS gender,
    maritalStatus        AS marital_status,
    CAST(startDate AS DATE) AS valid_from,
    load_date
FROM latest
ORDER BY person_id_external, valid_from
