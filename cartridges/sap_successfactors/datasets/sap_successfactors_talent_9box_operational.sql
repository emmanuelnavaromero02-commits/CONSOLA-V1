-- sap_successfactors_talent_9box_operational  (gold)  cartridge: sap_successfactors
-- sources: ["gold/sap_successfactors/sap_successfactors_talent_9box"]
-- description: Distribucion 9-box agregada para Control Room Talent. No expone PII ni identificadores individuales.

WITH boxes(box_key, potential_band, performance_band, box_label, movement_action, display_order) AS (
    VALUES
        ('enigma', 'high', 'low', 'Enigma', 'Cambio de rol o coaching de fit', 1),
        ('crecimiento', 'high', 'medium', 'Crecimiento', 'Asignacion de estiramiento y rotacion', 2),
        ('estrella', 'high', 'high', 'Estrella', 'Sucesion, promocion y retencion', 3),
        ('dilema', 'medium', 'low', 'Dilema', 'Plan de mejora o reubicacion', 4),
        ('core', 'medium', 'medium', 'Core', 'Retener y desarrollo continuo', 5),
        ('alto_impacto', 'medium', 'high', 'Alto Impacto', 'Promocion a siguiente nivel', 6),
        ('riesgo', 'low', 'low', 'Riesgo', 'PIP o gestion de salida', 7),
        ('efectivo', 'low', 'medium', 'Efectivo', 'Mantener en rol', 8),
        ('experto', 'low', 'high', 'Experto', 'Via tecnica y retencion en rol', 9)
),
rows AS (
    SELECT *
    FROM read_parquet('s3://{bucket}/gold/sap_successfactors/sap_successfactors_talent_9box/**/*.parquet',
                      hive_partitioning = true,
                      union_by_name = true)
),
metrics AS (
    SELECT
        box_key,
        COUNT(*) AS employee_count,
        COUNT(*) FILTER (
            WHERE box_status = 'ready'
              AND invalid_score_input IS FALSE
              AND talent_percent_is_valid(performance_score)
              AND talent_percent_is_valid(potential_score)
        ) AS ready_count,
        COUNT(*) FILTER (
            WHERE box_status = 'ready'
              AND source_mode = 'benchmark_internal'
              AND invalid_score_input IS FALSE
              AND talent_percent_is_valid(performance_score)
              AND talent_percent_is_valid(potential_score)
        ) AS benchmark_count,
        COUNT(*) FILTER (
            WHERE box_status <> 'ready'
               OR invalid_score_input IS DISTINCT FROM FALSE
               OR NOT talent_percent_is_valid(performance_score)
               OR NOT talent_percent_is_valid(potential_score)
        ) AS blocked_count
    FROM rows
    GROUP BY box_key
)
SELECT
    boxes.box_key,
    boxes.box_label,
    boxes.potential_band,
    boxes.performance_band,
    boxes.movement_action,
    COALESCE(metrics.employee_count, 0) AS employee_count,
    COALESCE(metrics.ready_count, 0) AS ready_count,
    COALESCE(metrics.benchmark_count, 0) AS benchmark_count,
    COALESCE(metrics.blocked_count, 0) AS blocked_count,
    CASE
        WHEN COALESCE(metrics.ready_count, 0) > 0 AND COALESCE(metrics.benchmark_count, 0) > 0 THEN 'benchmark_internal'
        WHEN COALESCE(metrics.ready_count, 0) > 0 THEN 'ready'
        ELSE 'empty'
    END AS box_status,
    boxes.display_order,
    CURRENT_TIMESTAMP AS generated_at
FROM boxes
LEFT JOIN metrics ON metrics.box_key = boxes.box_key
ORDER BY boxes.display_order
