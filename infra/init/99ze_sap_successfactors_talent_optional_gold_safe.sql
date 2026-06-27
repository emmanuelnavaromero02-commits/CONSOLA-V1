-- 99ze_sap_successfactors_talent_optional_gold_safe.sql
--
-- Talent Gold datasets must not hard-fail when tenant-specific optional
-- sources are unavailable. Keep foundation-derived Gold materializable and
-- expose blockers/insufficient_data instead of S3 404s from missing parquet.

UPDATE datasets
   SET sources = $sources$[
         "gold/sap_successfactors/sap_successfactors_employee_360",
         "gold/sap_successfactors/sap_successfactors_manager_hierarchy"
       ]$sources$::jsonb,
       sql_def = $sql$
-- sap_successfactors_talent_employee_profile  (gold)  cartridge: sap_successfactors
-- sources: ["gold/sap_successfactors/sap_successfactors_employee_360", "gold/sap_successfactors/sap_successfactors_manager_hierarchy"]
-- description: Perfil Talento foundation-safe. C/P/A queda insufficient_data cuando el tenant no expone fuentes opcionales.

WITH emp AS (
    SELECT *
    FROM read_parquet('s3://{bucket}/gold/sap_successfactors/sap_successfactors_employee_360/**/*.parquet',
                      hive_partitioning = true,
                      union_by_name = true)
    WHERE is_active = TRUE
),
hier AS (
    SELECT user_id, direct_reports, depth
    FROM read_parquet('s3://{bucket}/gold/sap_successfactors/sap_successfactors_manager_hierarchy/**/*.parquet',
                      hive_partitioning = true,
                      union_by_name = true)
)
SELECT
    emp.tenant_id,
    emp.workspace_id,
    emp.user_id,
    emp.full_name,
    emp.company_id,
    emp.company_name,
    emp.division_id,
    emp.division_name,
    emp.department_id,
    emp.department_name,
    emp.location_id,
    emp.location_name,
    emp.job_code,
    emp.manager_id,
    COALESCE(hier.direct_reports, 0) AS direct_reports,
    COALESCE(hier.depth, 0) AS hierarchy_depth,
    emp.start_date,
    emp.end_date,
    CASE
        WHEN emp.start_date IS NULL THEN NULL
        ELSE DATE_DIFF('month', TRY_CAST(emp.start_date AS DATE), CURRENT_DATE)
    END AS tenure_months,
    CAST(NULL AS DOUBLE) AS competency_score,
    CAST(NULL AS DOUBLE) AS performance_score,
    CAST(NULL AS DOUBLE) AS aspiration_score,
    'insufficient_data' AS cpa_status,
    'foundation_ready' AS profile_status,
    '["KB-COMPETENCIAS blocked","KB-DESEMPENO blocked","KB-ASPIRACION blocked"]' AS blockers,
    CURRENT_TIMESTAMP AS generated_at
FROM emp
LEFT JOIN hier ON hier.user_id = emp.user_id
ORDER BY emp.user_id
$sql$,
       description = 'Perfil Talento foundation-safe. C/P/A queda insufficient_data cuando faltan fuentes tenant-specific.',
       updated_at = NOW()
 WHERE cartridge = 'sap_successfactors'
   AND name = 'sap_successfactors_talent_employee_profile';

UPDATE datasets
   SET sources = $sources$[
         "gold/sap_successfactors/sap_successfactors_employee_360"
       ]$sources$::jsonb,
       sql_def = $sql$
-- sap_successfactors_talent_role_profile  (gold)  cartridge: sap_successfactors
-- sources: ["gold/sap_successfactors/sap_successfactors_employee_360"]
-- description: Perfil de rol foundation-safe derivado desde empleados. Requirements quedan bloqueados si no existe matriz rol-skill.

WITH emp AS (
    SELECT *
    FROM read_parquet('s3://{bucket}/gold/sap_successfactors/sap_successfactors_employee_360/**/*.parquet',
                      hive_partitioning = true,
                      union_by_name = true)
),
rollup AS (
    SELECT
        emp.job_code,
        COUNT(*) AS employee_count,
        COUNT(*) FILTER (WHERE emp.is_active = TRUE) AS active_employee_count,
        COUNT(DISTINCT emp.department_id) AS departments_count,
        COUNT(DISTINCT emp.location_id) AS locations_count,
        COUNT(DISTINCT emp.company_id) AS companies_count
    FROM emp
    WHERE emp.job_code IS NOT NULL
    GROUP BY emp.job_code
)
SELECT
    rollup.job_code,
    rollup.job_code AS role_name,
    rollup.employee_count,
    rollup.active_employee_count,
    rollup.departments_count,
    rollup.locations_count,
    rollup.companies_count,
    'blocked' AS required_skills_status,
    'partial' AS role_profile_status,
    '["Position requirements pending","Skills/competencies metadata pending"]' AS blockers,
    CURRENT_TIMESTAMP AS generated_at
FROM rollup
ORDER BY rollup.active_employee_count DESC, rollup.job_code
$sql$,
       description = 'Perfil de rol foundation-safe derivado desde empleados; requirements se reportan como blockers.',
       updated_at = NOW()
 WHERE cartridge = 'sap_successfactors'
   AND name = 'sap_successfactors_talent_role_profile';

UPDATE datasets
   SET sources = $sources$[
         "gold/sap_successfactors/sap_successfactors_talent_action_candidates",
         "gold/sap_successfactors/sap_successfactors_talent_role_profile",
         "gold/sap_successfactors/sap_successfactors_talent_mobility_history"
       ]$sources$::jsonb,
       sql_def = $sql$
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
    CURRENT_TIMESTAMP AS generated_at,
    'gold_ready' AS readiness_status,
    (SELECT action_count FROM metrics) AS source_row_count,
    CURRENT_TIMESTAMP AS materialized_at,
    '[]' AS blockers
FROM actions
UNION ALL
SELECT
    'talent_role_requirements_missing' AS signal_id,
    'pipeline' AS signal_type,
    'medium' AS severity,
    blocked_role_count AS affected_count,
    'Requisitos de rol pendientes' AS title,
    'Validar Position y entidades de skills para comparar persona contra rol.' AS recommendation,
    'recommendation_only' AS status,
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
    'Usar historial EmpJob como proxy temporal mientras aspiracion declarada queda pendiente.' AS recommendation,
    'recommendation_only' AS status,
    CURRENT_TIMESTAMP AS generated_at,
    'gold_ready' AS readiness_status,
    mobility_count AS source_row_count,
    CURRENT_TIMESTAMP AS materialized_at,
    '[]' AS blockers
FROM metrics
WHERE employees_with_mobility_count > 0
$sql$,
       description = 'Senales WisdomBit Talento foundation-safe con blockers explicitos para fuentes opcionales no materializadas.',
       updated_at = NOW()
 WHERE cartridge = 'sap_successfactors'
   AND name = 'sap_successfactors_talent_signals';

INSERT INTO schema_migrations (filename, applied_at)
VALUES ('99ze_sap_successfactors_talent_optional_gold_safe.sql', NOW())
ON CONFLICT (filename) DO NOTHING;
