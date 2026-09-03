-- sap_successfactors_talent_signals  (gold)  cartridge: sap_successfactors
-- sources: ["gold/sap_successfactors/sap_successfactors_talent_action_candidates", "gold/sap_successfactors/sap_successfactors_talent_role_profile", "gold/sap_successfactors/sap_successfactors_talent_mobility_history"]
-- description: Senales WisdomBit Talento foundation-safe. Fuentes opcionales quedan como blockers, no como error de materializacion.

WITH actions AS (
    SELECT *
    FROM read_parquet('s3://{bucket}/gold/sap_successfactors/sap_successfactors_talent_action_candidates/**/*.parquet',
                      hive_partitioning = true,
                      union_by_name = true)
),
roles AS (
    SELECT *
    FROM read_parquet('s3://{bucket}/gold/sap_successfactors/sap_successfactors_talent_role_profile/**/*.parquet',
                      hive_partitioning = true,
                      union_by_name = true)
),
mobility AS (
    SELECT *
    FROM read_parquet('s3://{bucket}/gold/sap_successfactors/sap_successfactors_talent_mobility_history/**/*.parquet',
                      hive_partitioning = true,
                      union_by_name = true)
),
metrics AS (
    SELECT
        (SELECT COUNT(*) FROM actions) AS action_count,
        (SELECT COUNT(*) FROM roles) AS role_count,
        (SELECT COUNT(*) FROM mobility) AS mobility_count,
        (SELECT COUNT(*) FROM roles WHERE required_skills_status = 'blocked') AS blocked_role_count,
        (SELECT COUNT(*) FROM mobility WHERE movement_events > 0) AS employees_with_mobility_count
)
SELECT
    action_id AS signal_id,
    action_type AS signal_type,
    severity,
    affected_count,
    title,
    recommendation,
    status,
    source_validation_status,
    CURRENT_TIMESTAMP AS generated_at,
    'gold_ready' AS readiness_status,
    (SELECT action_count FROM metrics) AS source_row_count,
    CURRENT_TIMESTAMP AS materialized_at,
    '[]' AS blockers
FROM actions
WHERE source_validation_status = 'server_validated_v1'
UNION ALL
SELECT
    'talent_role_requirements_missing' AS signal_id,
    'pipeline' AS signal_type,
    'medium' AS severity,
    blocked_role_count AS affected_count,
    'Requisitos de rol pendientes' AS title,
    'Validar Position y entidades de skills para comparar persona contra rol.' AS recommendation,
    'recommendation_only' AS status,
    'server_validated_v1' AS source_validation_status,
    CURRENT_TIMESTAMP AS generated_at,
    'partial' AS readiness_status,
    role_count AS source_row_count,
    CURRENT_TIMESTAMP AS materialized_at,
    '["Position requirements pending","Required skills pending"]' AS blockers
FROM metrics
WHERE blocked_role_count > 0
UNION ALL
SELECT
    'talent_mobility_observed' AS signal_id,
    'asignacion' AS signal_type,
    'low' AS severity,
    employees_with_mobility_count AS affected_count,
    'Movilidad observada disponible' AS title,
    'Conservar la movilidad como hecho observado; no usarla como proxy de aspiracion.' AS recommendation,
    'recommendation_only' AS status,
    'server_validated_v1' AS source_validation_status,
    CURRENT_TIMESTAMP AS generated_at,
    'gold_ready' AS readiness_status,
    mobility_count AS source_row_count,
    CURRENT_TIMESTAMP AS materialized_at,
    '[]' AS blockers
FROM metrics
WHERE employees_with_mobility_count > 0
