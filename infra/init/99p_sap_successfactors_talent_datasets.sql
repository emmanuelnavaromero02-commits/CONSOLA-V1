-- 99p_sap_successfactors_talent_datasets.sql
--
-- Existing AWS/local installs already applied 82_sap_successfactors_datasets_seed.sql.
-- This follow-up migration registers the SuccessFactors Talent/WisdomBit Gold
-- datasets without requiring the old seed to be replayed.

CREATE TABLE IF NOT EXISTS schema_migrations (
    id BIGSERIAL PRIMARY KEY,
    filename TEXT NOT NULL UNIQUE,
    applied_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    checksum TEXT
);

INSERT INTO datasets (name, layer, cartridge, sources, sql_def, description, column_mapping, schedule, updated_at, workspace_id)
VALUES
($seed$sap_successfactors_talent_employee_profile$seed$, $seed$gold$seed$, $seed$sap_successfactors$seed$, $seed$["gold/sap_successfactors/sap_successfactors_employee_360", "gold/sap_successfactors/sap_successfactors_manager_hierarchy"]$seed$::jsonb, $seed$
-- sap_successfactors_talent_employee_profile  (gold)  cartridge: sap_successfactors
-- sources: ["gold/sap_successfactors/sap_successfactors_employee_360", "gold/sap_successfactors/sap_successfactors_manager_hierarchy"]
-- description: Perfil Talento v1: empleado activo + estructura + jerarquia. C/P/A queda explicitamente en insufficient_data hasta activar entidades SAP de talento.

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
$seed$, $seed$Perfil Talento v1: empleado activo + estructura + jerarquia. C/P/A queda explicitamente en insufficient_data hasta activar entidades SAP de talento.$seed$, $seed${}$seed$::jsonb, $seed$$seed$, NOW(), (SELECT id FROM workspaces ORDER BY created_at ASC LIMIT 1)),
($seed$sap_successfactors_talent_role_profile$seed$, $seed$gold$seed$, $seed$sap_successfactors$seed$, $seed$["gold/sap_successfactors/sap_successfactors_employee_360", "silver/sap_successfactors/sap_successfactors_fojobcode_latest"]$seed$::jsonb, $seed$
-- sap_successfactors_talent_role_profile  (gold)  cartridge: sap_successfactors
-- sources: ["gold/sap_successfactors/sap_successfactors_employee_360", "silver/sap_successfactors/sap_successfactors_fojobcode_latest"]
-- description: Perfil de rol derivado desde job_code/FOJobCode. Requisitos de skills quedan bloqueados hasta validar metadata del tenant.

WITH emp AS (
    SELECT *
    FROM read_parquet('s3://{bucket}/gold/sap_successfactors/sap_successfactors_employee_360/**/*.parquet',
                      hive_partitioning = true,
                      union_by_name = true)
),
roles AS (
    SELECT job_code, job_name
    FROM read_parquet('s3://{bucket}/silver/sap_successfactors/sap_successfactors_fojobcode_latest/**/*.parquet',
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
    COALESCE(roles.job_name, rollup.job_code) AS role_name,
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
LEFT JOIN roles ON roles.job_code = rollup.job_code
ORDER BY rollup.active_employee_count DESC, rollup.job_code
$seed$, $seed$Perfil de rol derivado desde job_code/FOJobCode. Requisitos de skills quedan bloqueados hasta validar metadata del tenant.$seed$, $seed${}$seed$::jsonb, $seed$$seed$, NOW(), (SELECT id FROM workspaces ORDER BY created_at ASC LIMIT 1)),
($seed$sap_successfactors_talent_mobility_history$seed$, $seed$gold$seed$, $seed$sap_successfactors$seed$, $seed$["silver/sap_successfactors/sap_successfactors_empjob_latest", "gold/sap_successfactors/sap_successfactors_employee_360"]$seed$::jsonb, $seed$
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
$seed$, $seed$Movilidad basica desde historico efectivo de EmpJob. Sirve como senal observada, no como aspiracion declarada.$seed$, $seed${}$seed$::jsonb, $seed$$seed$, NOW(), (SELECT id FROM workspaces ORDER BY created_at ASC LIMIT 1)),
($seed$sap_successfactors_talent_readiness$seed$, $seed$gold$seed$, $seed$sap_successfactors$seed$, $seed$["gold/sap_successfactors/sap_successfactors_talent_employee_profile", "gold/sap_successfactors/sap_successfactors_talent_role_profile"]$seed$::jsonb, $seed$
-- sap_successfactors_talent_readiness  (gold)  cartridge: sap_successfactors
-- sources: ["gold/sap_successfactors/sap_successfactors_talent_employee_profile", "gold/sap_successfactors/sap_successfactors_talent_role_profile"]
-- description: Readiness Talento. En v1 conserva contrato estable y marca insufficient_data si faltan competencia, desempeno o aspiracion.

WITH emp AS (
    SELECT *
    FROM read_parquet('s3://{bucket}/gold/sap_successfactors/sap_successfactors_talent_employee_profile/**/*.parquet',
                      hive_partitioning = true,
                      union_by_name = true)
),
roles AS (
    SELECT job_code, role_name, role_profile_status, required_skills_status
    FROM read_parquet('s3://{bucket}/gold/sap_successfactors/sap_successfactors_talent_role_profile/**/*.parquet',
                      hive_partitioning = true,
                      union_by_name = true)
)
SELECT
    emp.user_id,
    emp.full_name,
    emp.company_name,
    emp.department_name,
    emp.location_name,
    emp.job_code,
    COALESCE(roles.role_name, emp.job_code) AS role_name,
    emp.competency_score,
    emp.performance_score,
    emp.aspiration_score,
    CAST(NULL AS DOUBLE) AS fit_score,
    'insufficient_data' AS readiness_status,
    'Insufficient data' AS readiness_label,
    COALESCE(roles.role_profile_status, 'partial') AS role_profile_status,
    COALESCE(roles.required_skills_status, 'blocked') AS required_skills_status,
    3 AS blocker_count,
    emp.blockers,
    CURRENT_TIMESTAMP AS generated_at
FROM emp
LEFT JOIN roles ON roles.job_code = emp.job_code
ORDER BY emp.user_id
$seed$, $seed$Readiness Talento. En v1 conserva contrato estable y marca insufficient_data si faltan competencia, desempeno o aspiracion.$seed$, $seed${}$seed$::jsonb, $seed$$seed$, NOW(), (SELECT id FROM workspaces ORDER BY created_at ASC LIMIT 1)),
($seed$sap_successfactors_talent_9box$seed$, $seed$gold$seed$, $seed$sap_successfactors$seed$, $seed$["gold/sap_successfactors/sap_successfactors_talent_readiness"]$seed$::jsonb, $seed$
-- sap_successfactors_talent_9box  (gold)  cartridge: sap_successfactors
-- sources: ["gold/sap_successfactors/sap_successfactors_talent_readiness"]
-- description: 9-box Talento con contrato estable. Se bloquea hasta contar con performance, competencias y aspiracion.

WITH readiness AS (
    SELECT *
    FROM read_parquet('s3://{bucket}/gold/sap_successfactors/sap_successfactors_talent_readiness/**/*.parquet',
                      hive_partitioning = true,
                      union_by_name = true)
)
SELECT
    user_id,
    full_name,
    company_name,
    department_name,
    location_name,
    job_code,
    role_name,
    performance_score,
    CAST(NULL AS DOUBLE) AS potential_score,
    'insufficient_data' AS performance_band,
    'insufficient_data' AS potential_band,
    'Sin datos C/P/A' AS box_label,
    'blocked' AS box_status,
    blockers,
    CURRENT_TIMESTAMP AS generated_at
FROM readiness
ORDER BY user_id
$seed$, $seed$9-box Talento con contrato estable. Se bloquea hasta contar con performance, competencias y aspiracion.$seed$, $seed${}$seed$::jsonb, $seed$$seed$, NOW(), (SELECT id FROM workspaces ORDER BY created_at ASC LIMIT 1)),
($seed$sap_successfactors_talent_signals$seed$, $seed$gold$seed$, $seed$sap_successfactors$seed$, $seed$["gold/sap_successfactors/sap_successfactors_talent_readiness", "gold/sap_successfactors/sap_successfactors_talent_role_profile", "gold/sap_successfactors/sap_successfactors_talent_mobility_history"]$seed$::jsonb, $seed$
-- sap_successfactors_talent_signals  (gold)  cartridge: sap_successfactors
-- sources: ["gold/sap_successfactors/sap_successfactors_talent_readiness", "gold/sap_successfactors/sap_successfactors_talent_role_profile", "gold/sap_successfactors/sap_successfactors_talent_mobility_history"]
-- description: Senales WisdomBit Talento como recomendaciones. No ejecuta acciones automaticas ni write-back.

WITH readiness AS (
    SELECT *
    FROM read_parquet('s3://{bucket}/gold/sap_successfactors/sap_successfactors_talent_readiness/**/*.parquet',
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
        (SELECT COUNT(*) FROM readiness WHERE readiness_status = 'insufficient_data') AS insufficient_readiness_count,
        (SELECT COUNT(*) FROM roles WHERE required_skills_status = 'blocked') AS blocked_role_count,
        (SELECT COUNT(*) FROM mobility WHERE movement_events > 0) AS employees_with_mobility_count
)
SELECT
    'talent_cpa_missing_inputs' AS signal_id,
    'priorizacion' AS signal_type,
    'medium' AS severity,
    insufficient_readiness_count AS affected_count,
    'Fit Score bloqueado por falta de C/P/A' AS title,
    'Habilitar desempeno, competencias y aspiracion para calcular readiness real.' AS recommendation,
    'recommendation_only' AS status,
    CURRENT_TIMESTAMP AS generated_at
FROM metrics
WHERE insufficient_readiness_count > 0
UNION ALL
SELECT
    'talent_role_requirements_missing' AS signal_id,
    'pipeline' AS signal_type,
    'medium' AS severity,
    blocked_role_count AS affected_count,
    'Requisitos de rol pendientes' AS title,
    'Validar Position y entidades de skills para comparar persona contra rol.' AS recommendation,
    'recommendation_only' AS status,
    CURRENT_TIMESTAMP AS generated_at
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
    CURRENT_TIMESTAMP AS generated_at
FROM metrics
WHERE employees_with_mobility_count > 0
$seed$, $seed$Senales WisdomBit Talento como recomendaciones. No ejecuta acciones automaticas ni write-back.$seed$, $seed${}$seed$::jsonb, $seed$$seed$, NOW(), (SELECT id FROM workspaces ORDER BY created_at ASC LIMIT 1))
ON CONFLICT (name) DO UPDATE
SET layer = EXCLUDED.layer,
    cartridge = EXCLUDED.cartridge,
    sources = EXCLUDED.sources,
    sql_def = EXCLUDED.sql_def,
    description = EXCLUDED.description,
    column_mapping = EXCLUDED.column_mapping,
    schedule = EXCLUDED.schedule,
    updated_at = NOW(),
    workspace_id = COALESCE(datasets.workspace_id, EXCLUDED.workspace_id);

INSERT INTO schema_migrations (filename, applied_at)
VALUES ('99p_sap_successfactors_talent_datasets.sql', NOW())
ON CONFLICT (filename) DO NOTHING;
