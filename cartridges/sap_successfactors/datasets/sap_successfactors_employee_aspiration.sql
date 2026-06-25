-- sap_successfactors_employee_aspiration  (silver)  cartridge: sap_successfactors
-- sources: ["silver/sap_successfactors/sap_successfactors_careerworksheet_latest", "silver/sap_successfactors/sap_successfactors_careerinterest_latest", "silver/sap_successfactors/sap_successfactors_successionnomination_latest"]
-- description: Aspiracion declarada, intereses de carrera y nominaciones de sucesion por empleado.

WITH worksheet AS (
    SELECT
        user_id,
        aspiration_record_id,
        target_role,
        readiness,
        CAST(NULL AS VARCHAR) AS mobility_preference,
        'CareerWorksheet' AS source_entity,
        load_date
    FROM read_parquet('s3://{bucket}/silver/sap_successfactors/sap_successfactors_careerworksheet_latest/**/*.parquet',
                      hive_partitioning = true,
                      union_by_name = true)
),
interest AS (
    SELECT
        user_id,
        aspiration_record_id,
        target_role,
        interest_level AS readiness,
        mobility_preference,
        'CareerInterest' AS source_entity,
        load_date
    FROM read_parquet('s3://{bucket}/silver/sap_successfactors/sap_successfactors_careerinterest_latest/**/*.parquet',
                      hive_partitioning = true,
                      union_by_name = true)
),
succession AS (
    SELECT
        user_id,
        nomination_id AS aspiration_record_id,
        target_position AS target_role,
        readiness,
        CAST(NULL AS VARCHAR) AS mobility_preference,
        'SuccessionNomination' AS source_entity,
        load_date
    FROM read_parquet('s3://{bucket}/silver/sap_successfactors/sap_successfactors_successionnomination_latest/**/*.parquet',
                      hive_partitioning = true,
                      union_by_name = true)
),
combined AS (
    SELECT * FROM worksheet
    UNION ALL
    SELECT * FROM interest
    UNION ALL
    SELECT * FROM succession
)
SELECT
    user_id,
    aspiration_record_id,
    target_role,
    readiness,
    CASE
        WHEN TRY_CAST(readiness AS DOUBLE) IS NULL THEN NULL
        WHEN TRY_CAST(readiness AS DOUBLE) <= 5 THEN TRY_CAST(readiness AS DOUBLE) * 20
        ELSE TRY_CAST(readiness AS DOUBLE)
    END AS aspiration_100,
    mobility_preference,
    source_entity,
    CASE
        WHEN readiness IS NULL AND target_role IS NULL THEN 'insufficient_data'
        ELSE 'ready'
    END AS aspiration_status,
    load_date
FROM combined
WHERE user_id IS NOT NULL
ORDER BY user_id, target_role
