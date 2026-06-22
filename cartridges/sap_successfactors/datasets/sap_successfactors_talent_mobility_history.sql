-- sap_successfactors_talent_mobility_history  (gold)  cartridge: sap_successfactors
-- sources: ["silver/sap_successfactors/sap_successfactors_empjob_latest", "gold/sap_successfactors/sap_successfactors_employee_360"]
-- description: Movilidad basica desde historico efectivo de EmpJob. Sirve como senal observada, no como aspiracion declarada.

WITH job_history AS (
    SELECT *
    FROM read_parquet('s3://{bucket}/silver/sap_successfactors/sap_successfactors_empjob_latest/**/*.parquet',
                      hive_partitioning = true,
                      union_by_name = true)
    WHERE user_id IS NOT NULL
),
latest_event AS (
    SELECT
        user_id,
        event_reason AS latest_event_reason
    FROM (
        SELECT
            user_id,
            event_reason,
            ROW_NUMBER() OVER (
                PARTITION BY user_id
                ORDER BY TRY_CAST(start_date AS DATE) DESC NULLS LAST
            ) AS rn
        FROM job_history
    )
    WHERE rn = 1
),
rollup AS (
    SELECT
        user_id,
        MIN(TRY_CAST(start_date AS DATE)) AS first_assignment_date,
        MAX(TRY_CAST(start_date AS DATE)) AS latest_assignment_date,
        GREATEST(COUNT(*) - 1, 0) AS movement_events,
        COUNT(DISTINCT department) AS distinct_departments,
        COUNT(DISTINCT location) AS distinct_locations,
        COUNT(DISTINCT job_code) AS distinct_job_codes,
        COUNT(DISTINCT manager_id) AS distinct_managers
    FROM job_history
    GROUP BY user_id
),
emp AS (
    SELECT user_id, full_name, company_name, department_name, location_name, job_code, is_active
    FROM read_parquet('s3://{bucket}/gold/sap_successfactors/sap_successfactors_employee_360/**/*.parquet',
                      hive_partitioning = true,
                      union_by_name = true)
)
SELECT
    rollup.user_id,
    emp.full_name,
    emp.company_name,
    emp.department_name,
    emp.location_name,
    emp.job_code,
    emp.is_active,
    rollup.first_assignment_date,
    rollup.latest_assignment_date,
    rollup.movement_events,
    rollup.distinct_departments,
    rollup.distinct_locations,
    rollup.distinct_job_codes,
    rollup.distinct_managers,
    latest_event.latest_event_reason,
    'observed_from_empjob' AS mobility_status,
    CURRENT_TIMESTAMP AS generated_at
FROM rollup
LEFT JOIN emp ON emp.user_id = rollup.user_id
LEFT JOIN latest_event ON latest_event.user_id = rollup.user_id
ORDER BY rollup.movement_events DESC, rollup.user_id
