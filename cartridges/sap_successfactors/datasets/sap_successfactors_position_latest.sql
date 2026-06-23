-- sap_successfactors_position_latest  (silver)  cartridge: sap_successfactors
-- sources: ["raw/sap_successfactors/Position"]
-- description: Última extracción de Position Management (posiciones).

-- NOTA: Position no declara select_fields; campos SF estándar (code, nombre, org).
WITH raw AS (
    SELECT *
    FROM read_parquet('s3://{bucket}/raw/sap_successfactors/Position/**/*.parquet',
                      hive_partitioning = true,
                      union_by_name   = true)
),
latest AS (
    SELECT *
    FROM (
        SELECT
            raw.*,
            ROW_NUMBER() OVER (
                PARTITION BY code
                ORDER BY
                    TRY_CAST(lastModifiedDateTime AS TIMESTAMP) DESC NULLS LAST,
                    TRY_CAST(_extracted_at AS TIMESTAMP) DESC NULLS LAST,
                    TRY_CAST(load_date AS DATE) DESC NULLS LAST,
                    CAST(batch_id AS VARCHAR) DESC NULLS LAST
            ) AS _rn
        FROM raw
        WHERE code IS NOT NULL
    )
    WHERE _rn = 1
)
SELECT
    code                       AS position_id,
    externalName_defaultValue  AS position_name,
    department                 AS department,
    location                   AS location,
    costCenter                 AS cost_center,
    load_date
FROM latest
ORDER BY position_id
