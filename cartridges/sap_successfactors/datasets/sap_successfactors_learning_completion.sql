-- sap_successfactors_learning_completion  (silver)  cartridge: sap_successfactors
-- sources: ["silver/sap_successfactors/sap_successfactors_learningassignment_latest", "silver/sap_successfactors/sap_successfactors_learninghistory_latest", "silver/sap_successfactors/sap_successfactors_learningitem_latest"]
-- description: Aprendizaje por empleado con asignaciones, completitud y horas observadas.

WITH assignments AS (
    SELECT *
    FROM read_parquet('s3://{bucket}/silver/sap_successfactors/sap_successfactors_learningassignment_latest/**/*.parquet',
                      hive_partitioning = true,
                      union_by_name = true)
),
history AS (
    SELECT *
    FROM read_parquet('s3://{bucket}/silver/sap_successfactors/sap_successfactors_learninghistory_latest/**/*.parquet',
                      hive_partitioning = true,
                      union_by_name = true)
),
items AS (
    SELECT *
    FROM read_parquet('s3://{bucket}/silver/sap_successfactors/sap_successfactors_learningitem_latest/**/*.parquet',
                      hive_partitioning = true,
                      union_by_name = true)
),
combined AS (
    SELECT
        assignment_id AS learning_event_id,
        user_id,
        learning_item_id,
        status,
        due_date,
        completion_date,
        CAST(NULL AS DOUBLE) AS credit_hours,
        'LearningAssignment' AS source_entity,
        load_date
    FROM assignments
    UNION ALL
    SELECT
        history_id AS learning_event_id,
        user_id,
        learning_item_id,
        status,
        CAST(NULL AS DATE) AS due_date,
        completion_date,
        credit_hours,
        'LearningHistory' AS source_entity,
        load_date
    FROM history
)
SELECT
    c.learning_event_id,
    c.user_id,
    c.learning_item_id,
    i.title,
    c.status,
    c.due_date,
    c.completion_date,
    COALESCE(c.credit_hours, i.credit_hours) AS credit_hours,
    CASE
        WHEN c.completion_date IS NOT NULL OR LOWER(COALESCE(c.status, '')) IN ('completed', 'complete') THEN TRUE
        ELSE FALSE
    END AS completed,
    CASE
        WHEN c.due_date IS NOT NULL AND c.due_date < CURRENT_DATE
             AND NOT (c.completion_date IS NOT NULL OR LOWER(COALESCE(c.status, '')) IN ('completed', 'complete'))
            THEN TRUE
        ELSE FALSE
    END AS overdue,
    c.source_entity,
    c.load_date
FROM combined c
LEFT JOIN items i ON i.learning_item_id = c.learning_item_id
ORDER BY c.user_id, c.learning_item_id
