-- sap_successfactors_movement_events  (silver)  cartridge: sap_successfactors
-- sources: ["silver/sap_successfactors/sap_successfactors_empjob_latest", "silver/sap_successfactors/sap_successfactors_foeventreason_latest"]
-- description: Eventos de movilidad desde EmpJob enriquecidos con FOEventReason.

WITH jobs AS (
    SELECT *
    FROM read_parquet('s3://{bucket}/silver/sap_successfactors/sap_successfactors_empjob_latest/**/*.parquet',
                      hive_partitioning = true,
                      union_by_name = true)
),
reasons AS (
    SELECT
        CAST(event_reason_id AS VARCHAR) AS event_reason_id,
        CAST(event_reason_name AS VARCHAR) AS event_reason_name,
        CAST(event AS VARCHAR) AS event,
        CAST(event_reason_category AS VARCHAR) AS event_reason_category
    FROM read_parquet('s3://{bucket}/silver/sap_successfactors/sap_successfactors_foeventreason_latest/**/*.parquet',
                      hive_partitioning = true,
                      union_by_name = true)
)
SELECT
    j.user_id,
    j.start_date AS event_date,
    j.job_code,
    j.position,
    j.department,
    j.location,
    j.manager_id,
    CAST(j.event_reason AS VARCHAR) AS event_reason,
    r.event_reason_name,
    r.event,
    COALESCE(r.event_reason_category, 'unclassified') AS event_reason_category,
    CASE
        WHEN LOWER(COALESCE(r.event_reason_category, CAST(j.event_reason AS VARCHAR), '')) LIKE '%promotion%' THEN 'promotion'
        WHEN LOWER(COALESCE(r.event_reason_category, CAST(j.event_reason AS VARCHAR), '')) LIKE '%transfer%' THEN 'transfer'
        WHEN LOWER(COALESCE(r.event_reason_category, CAST(j.event_reason AS VARCHAR), '')) LIKE '%lateral%' THEN 'lateral'
        WHEN LOWER(COALESCE(r.event_reason_category, CAST(j.event_reason AS VARCHAR), '')) LIKE '%demotion%' THEN 'demotion'
        ELSE 'movement'
    END AS movement_type,
    j.load_date
FROM jobs j
LEFT JOIN reasons r ON r.event_reason_id = CAST(j.event_reason AS VARCHAR)
WHERE j.event_reason IS NOT NULL
ORDER BY j.user_id, j.start_date DESC NULLS LAST
