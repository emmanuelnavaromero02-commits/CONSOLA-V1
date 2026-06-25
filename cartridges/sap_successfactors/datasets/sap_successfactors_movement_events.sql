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
    SELECT *
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
    j.event_reason,
    r.event_reason_name,
    r.event,
    COALESCE(r.event_reason_category, 'unclassified') AS event_reason_category,
    CASE
        WHEN LOWER(COALESCE(r.event_reason_category, j.event_reason, '')) LIKE '%promotion%' THEN 'promotion'
        WHEN LOWER(COALESCE(r.event_reason_category, j.event_reason, '')) LIKE '%transfer%' THEN 'transfer'
        WHEN LOWER(COALESCE(r.event_reason_category, j.event_reason, '')) LIKE '%lateral%' THEN 'lateral'
        WHEN LOWER(COALESCE(r.event_reason_category, j.event_reason, '')) LIKE '%demotion%' THEN 'demotion'
        ELSE 'movement'
    END AS movement_type,
    j.load_date
FROM jobs j
LEFT JOIN reasons r ON r.event_reason_id = j.event_reason
WHERE j.event_reason IS NOT NULL
ORDER BY j.user_id, j.start_date DESC NULLS LAST
