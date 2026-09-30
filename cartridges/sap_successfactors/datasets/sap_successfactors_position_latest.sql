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
optional_columns AS (
    SELECT
        CAST(NULL AS VARCHAR) AS criticality,
        CAST(NULL AS VARCHAR) AS positionCriticality,
        CAST(NULL AS VARCHAR) AS vacant,
        CAST(NULL AS VARCHAR) AS effectiveStatus
    LIMIT 0
),
raw_with_optional AS (
    SELECT * FROM optional_columns
    UNION ALL BY NAME
    SELECT * FROM raw
),
latest AS (
    SELECT *
    FROM (
        SELECT
            raw_with_optional.*,
            ROW_NUMBER() OVER (
                PARTITION BY code
                ORDER BY
                    sf_odata_timestamp(lastModifiedDateTime) DESC NULLS LAST,
                    TRY_CAST(_extracted_at AS TIMESTAMP) DESC NULLS LAST,
                    TRY_CAST(load_date AS DATE) DESC NULLS LAST,
                    CAST(batch_id AS VARCHAR) DESC NULLS LAST
            ) AS _rn
        FROM raw_with_optional
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
    COALESCE(
        NULLIF(TRIM(CAST(positionCriticality AS VARCHAR)), ''),
        NULLIF(TRIM(CAST(criticality AS VARCHAR)), '')
    )                          AS criticality,
    CASE
        WHEN LOWER(TRIM(CAST(vacant AS VARCHAR))) IN ('true', 't', '1', 'yes', 'y') THEN TRUE
        WHEN LOWER(TRIM(CAST(vacant AS VARCHAR))) IN ('false', 'f', '0', 'no', 'n') THEN FALSE
    END                        AS is_vacant,
    NULLIF(TRIM(CAST(effectiveStatus AS VARCHAR)), '') AS effective_status,
    load_date
FROM latest
ORDER BY position_id
