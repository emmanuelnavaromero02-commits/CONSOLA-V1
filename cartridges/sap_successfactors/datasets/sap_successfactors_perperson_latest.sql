-- sap_successfactors_perperson_latest  (silver)  cartridge: sap_successfactors
-- sources: ["raw/sap_successfactors/PerPerson"]
-- description: Última extracción de PerPerson (persona EC). personIdExternal shadowed y dateOfBirth encrypted desde bronze (caja negra, no agregable).

WITH latest AS (
    SELECT *
    FROM read_parquet('s3://{bucket}/raw/sap_successfactors/PerPerson/**/*.parquet',
                      hive_partitioning = true,
                      union_by_name   = true)
    WHERE load_date = (SELECT MAX(load_date)
                       FROM read_parquet('s3://{bucket}/raw/sap_successfactors/PerPerson/**/*.parquet',
                                          hive_partitioning = true))
)
SELECT
    personIdExternal     AS person_id_external,  -- shadowed en bronze
    personId             AS person_id,
    dateOfBirth          AS date_of_birth,        -- encrypted en bronze (token Fernet)
    countryOfBirth       AS country_of_birth,
    load_date
FROM latest
ORDER BY person_id_external
