-- sap_successfactors_employee_360  (gold)  cartridge: sap_successfactors
-- sources: ["silver/sap_successfactors/sap_successfactors_empemployment_latest", "silver/sap_successfactors/sap_successfactors_empjob_latest", "silver/sap_successfactors/sap_successfactors_perpersonal_latest", "silver/sap_successfactors/sap_successfactors_focompany_latest", "silver/sap_successfactors/sap_successfactors_fodepartment_latest", "silver/sap_successfactors/sap_successfactors_fodivision_latest", "silver/sap_successfactors/sap_successfactors_folocation_latest"]
-- description: Vista 360 del empleado activo: empleo + puesto + nombre (PerPersonal) + nombres de org. Una fila por empleado.

-- NOTA de privacidad: se une por claves PLANAS (EmpEmployment/EmpJob.user_id y
-- EmpEmployment.person_id_external). User y PerPerson quedan FUERA porque su
-- clave está shadowed (hash) y no casa con las planas — artefacto del Bloque A.
-- Regla de actividad: Considera activo si end_date IS NULL o end_date >= hoy.
-- Las filas con start_date NULL se asumen activas pendiente de validación contra
-- EmpEmploymentTermination. Las filas con end_date < hoy NO cuentan como activas.
WITH emp AS (
    SELECT user_id, person_id_external, start_date, end_date,
           ROW_NUMBER() OVER (PARTITION BY user_id ORDER BY start_date DESC) AS rn
    FROM read_parquet('s3://{bucket}/silver/sap_successfactors/sap_successfactors_empemployment_latest/**/*.parquet')
),
job AS (
    SELECT user_id, job_code, department, division, location, company, cost_center, manager_id,
           ROW_NUMBER() OVER (PARTITION BY user_id ORDER BY start_date DESC) AS rn
    FROM read_parquet('s3://{bucket}/silver/sap_successfactors/sap_successfactors_empjob_latest/**/*.parquet')
),
pers AS (
    SELECT person_id_external,
           TRIM(COALESCE(first_name, '') || ' ' || COALESCE(last_name, '')) AS full_name,
           gender, marital_status,
           ROW_NUMBER() OVER (PARTITION BY person_id_external ORDER BY valid_from DESC) AS rn
    FROM read_parquet('s3://{bucket}/silver/sap_successfactors/sap_successfactors_perpersonal_latest/**/*.parquet')
),
company AS (SELECT company_id, company_name FROM read_parquet('s3://{bucket}/silver/sap_successfactors/sap_successfactors_focompany_latest/**/*.parquet')),
dept AS (SELECT department_id, department_name FROM read_parquet('s3://{bucket}/silver/sap_successfactors/sap_successfactors_fodepartment_latest/**/*.parquet')),
divi AS (SELECT division_id, division_name FROM read_parquet('s3://{bucket}/silver/sap_successfactors/sap_successfactors_fodivision_latest/**/*.parquet')),
loc AS (SELECT location_id, location_name FROM read_parquet('s3://{bucket}/silver/sap_successfactors/sap_successfactors_folocation_latest/**/*.parquet'))
SELECT
    e.user_id                       AS user_id,            -- plano
    -- Clave tecnica para joins internos de talento. Replica el shadowing de
    -- Performance (protection_service._shadow = sha256 hex) para poder unir con
    -- performance_cycle/competency/aspiration, cuyo user_id viene shadowed. No
    -- expone PII: es el mismo hash irreversible del userId ya crudo aqui.
    sha256(CAST(e.user_id AS VARCHAR)) AS user_id_hash,
    p.full_name                     AS full_name,          -- masked
    p.gender                        AS gender,
    p.marital_status                AS marital_status,
    j.company                       AS company_id,
    co.company_name                 AS company_name,
    j.division                      AS division_id,
    dv.division_name                AS division_name,
    j.department                    AS department_id,
    d.department_name               AS department_name,
    j.location                      AS location_id,
    l.location_name                 AS location_name,
    j.job_code                      AS job_code,
    j.cost_center                   AS cost_center,
    j.manager_id                    AS manager_id,         -- plano (para manager_hierarchy)
    e.start_date                    AS start_date,
    e.end_date                      AS end_date,
    CASE WHEN (e.start_date IS NULL OR e.start_date <= CURRENT_DATE)
           AND (e.end_date IS NULL OR e.end_date >= CURRENT_DATE)
         THEN TRUE ELSE FALSE END   AS is_active
FROM emp e
LEFT JOIN job j  ON j.user_id = e.user_id AND j.rn = 1
LEFT JOIN pers p ON p.person_id_external = e.person_id_external AND p.rn = 1
LEFT JOIN company co ON co.company_id = j.company
LEFT JOIN dept d ON d.department_id = j.department
LEFT JOIN divi dv ON dv.division_id = j.division
LEFT JOIN loc l  ON l.location_id = j.location
WHERE e.rn = 1
ORDER BY e.user_id
