-- sap_successfactors_usercourses_latest  (silver)  cartridge: sap_successfactors
-- sources: ["raw/sap_successfactors/UserCourses"]
-- description: Ultima extraccion de asignaciones Learning v4 de cursos por usuario.

WITH raw AS (
    SELECT *
    FROM read_parquet('s3://{bucket}/raw/sap_successfactors/UserCourses/**/*.parquet',
                      hive_partitioning = true,
                      union_by_name = true)
),
latest AS (
    SELECT *
    FROM (
        SELECT
            raw.*,
            ROW_NUMBER() OVER (
                PARTITION BY assignmentId
                ORDER BY
                    TRY_CAST(lastModifiedDateTime AS TIMESTAMP) DESC NULLS LAST,
                    TRY_CAST(_extracted_at AS TIMESTAMP) DESC NULLS LAST,
                    TRY_CAST(load_date AS DATE) DESC NULLS LAST,
                    CAST(batch_id AS VARCHAR) DESC NULLS LAST
            ) AS _rn
        FROM raw
        WHERE assignmentId IS NOT NULL
    )
    WHERE _rn = 1
)
SELECT
    assignmentId AS assignment_id,
    userId AS user_id,
    courseId AS item_id,
    status,
    TRY_CAST(dueDate AS DATE) AS due_date,
    TRY_CAST(completionDate AS DATE) AS completion_date,
    load_date
FROM latest
ORDER BY user_id, assignment_id
