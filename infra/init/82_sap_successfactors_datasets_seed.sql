-- 82_sap_successfactors_datasets_seed.sql
--
-- Seeds the SAP SuccessFactors silver/gold datasets into the `datasets` catalog.
-- Mirrors infra/init/80_sap_hcm_datasets_seed.sql and 81_sap_s4hana_datasets_seed.sql:
-- the per-dataset SQL bodies live in cartridges/sap_successfactors/datasets/*.sql
-- and are inlined here as dollar-quoted literals for refinement to materialize.
--
-- All names are prefixed sap_successfactors_ : datasets.name is a GLOBAL primary
-- key and headcount_by_department / manager_hierarchy / employees_anomalies
-- already exist for sap_hcm.
--
-- workspace_id (NOT NULL since migration 23) is set to the first workspace on
-- every row (the lesson from the HCM datasets PR).
--
-- 24 silver + 8 gold = 32 datasets. Idempotent: ON CONFLICT (name) DO NOTHING.

INSERT INTO datasets (name, layer, cartridge, sources, sql_def, description, column_mapping, schedule, updated_at, workspace_id)
VALUES
($seed$sap_successfactors_candidate_latest$seed$, $seed$silver$seed$, $seed$sap_successfactors$seed$, $seed$["raw/sap_successfactors/Candidate"]$seed$::jsonb, $seed$
-- sap_successfactors_candidate_latest  (silver)  cartridge: sap_successfactors
-- sources: ["raw/sap_successfactors/Candidate"]
-- description: Última extracción de candidatos (Recruiting). candidateId shadowed y nombre masked desde bronze.

WITH raw AS (
    SELECT *
    FROM read_parquet('s3://{bucket}/raw/sap_successfactors/Candidate/**/*.parquet',
                      hive_partitioning = true,
                      union_by_name   = true)
),
latest AS (
    SELECT *
    FROM (
        SELECT
            raw.*,
            ROW_NUMBER() OVER (
                PARTITION BY candidateId
                ORDER BY
                    TRY_CAST(lastModifiedDateTime AS TIMESTAMP) DESC NULLS LAST,
                    TRY_CAST(_extracted_at AS TIMESTAMP) DESC NULLS LAST,
                    TRY_CAST(load_date AS DATE) DESC NULLS LAST,
                    CAST(batch_id AS VARCHAR) DESC NULLS LAST
            ) AS _rn
        FROM raw
        WHERE candidateId IS NOT NULL
    )
    WHERE _rn = 1
)
SELECT
    candidateId          AS candidate_id,       -- shadowed en bronze (FK)
    firstName            AS first_name,         -- masked en bronze
    lastName             AS last_name,          -- masked en bronze
    status               AS status,
    load_date
FROM latest
ORDER BY candidate_id
$seed$, $seed$Última extracción de candidatos (Recruiting). candidateId shadowed y nombre masked desde bronze.$seed$, $seed${}$seed$::jsonb, $seed$$seed$, NOW(), (SELECT id FROM workspaces ORDER BY created_at ASC LIMIT 1)),
($seed$sap_successfactors_compensation_distribution$seed$, $seed$gold$seed$, $seed$sap_successfactors$seed$, $seed$["raw/sap_successfactors/EmpPayCompRecurring", "raw/sap_successfactors/EmpJob"]$seed$::jsonb, $seed$
-- sap_successfactors_compensation_distribution  (gold)  cartridge: sap_successfactors
-- sources: ["raw/sap_successfactors/EmpPayCompRecurring", "raw/sap_successfactors/EmpJob"]
-- description: Distribución de compensación (cuartiles/mediana) por departamento y job_code. PENDIENTE: paycompValue está encrypted en bronze y no es agregable en SQL.

-- TODO: paycompValue está encrypted en bronze (token Fernet), por lo que NO se
-- puede calcular cuartiles/mediana en SQL. Para habilitarlo, cambiar la regla de
-- protección de paycompValue: 'plain' permite agregación en claro (requiere
-- revisión de privacidad); 'shadowed' no ayuda (hash no ordenable). Hoy devuelve
-- el schema vacío para que los consumidores no rompan.
SELECT
    CAST(NULL AS VARCHAR)        AS department_id,
    CAST(NULL AS VARCHAR)        AS job_code,
    CAST(NULL AS DECIMAL(15,2))  AS p25,
    CAST(NULL AS DECIMAL(15,2))  AS median,
    CAST(NULL AS DECIMAL(15,2))  AS p75,
    CAST(NULL AS BIGINT)         AS employee_count
WHERE FALSE
$seed$, $seed$Distribución de compensación (cuartiles/mediana) por departamento y job_code. PENDIENTE: paycompValue está encrypted en bronze y no es agregable en SQL.$seed$, $seed${}$seed$::jsonb, $seed$$seed$, NOW(), (SELECT id FROM workspaces ORDER BY created_at ASC LIMIT 1)),
($seed$sap_successfactors_compensation_full$seed$, $seed$silver$seed$, $seed$sap_successfactors$seed$, $seed$["raw/sap_successfactors/EmpCompensation", "raw/sap_successfactors/EmpPayCompRecurring", "raw/sap_successfactors/EmpPayCompNonRecurring", "silver/sap_successfactors/sap_successfactors_empcompensation_latest", "silver/sap_successfactors/sap_successfactors_emppaycomprecurring_latest", "silver/sap_successfactors/sap_successfactors_emppaycompnonrecurring_latest"]$seed$::jsonb, $seed$
-- sap_successfactors_compensation_full  (silver)  cartridge: sap_successfactors
-- sources: ["raw/sap_successfactors/EmpCompensation", "raw/sap_successfactors/EmpPayCompRecurring", "raw/sap_successfactors/EmpPayCompNonRecurring", "silver/sap_successfactors/sap_successfactors_empcompensation_latest", "silver/sap_successfactors/sap_successfactors_emppaycomprecurring_latest", "silver/sap_successfactors/sap_successfactors_emppaycompnonrecurring_latest"]
-- description: Componentes de compensación por empleado (cabecera + recurrentes + no recurrentes). El importe (paycomp_value) llega encrypted desde bronze: se conserva como caja negra y NO es agregable.

-- Unión por user_id (plano en las tres entidades). paycomp_value es un token
-- cifrado: sirve para trazabilidad fila a fila, no para sumas/medias en gold.
WITH header AS (
    SELECT user_id, pay_group, frequency_code,
           ROW_NUMBER() OVER (PARTITION BY user_id ORDER BY start_date DESC) AS rn
    FROM read_parquet('s3://{bucket}/silver/sap_successfactors/sap_successfactors_empcompensation_latest/**/*.parquet')
),
recurring AS (
    SELECT user_id, pay_component, paycomp_value, currency, 'recurring' AS kind
    FROM read_parquet('s3://{bucket}/silver/sap_successfactors/sap_successfactors_emppaycomprecurring_latest/**/*.parquet')
),
non_recurring AS (
    SELECT user_id, pay_component, paycomp_value, currency, 'non_recurring' AS kind
    FROM read_parquet('s3://{bucket}/silver/sap_successfactors/sap_successfactors_emppaycompnonrecurring_latest/**/*.parquet')
),
components AS (
    SELECT * FROM recurring
    UNION ALL
    SELECT * FROM non_recurring
)
SELECT
    cmp.user_id          AS user_id,            -- plano
    h.pay_group          AS pay_group,
    h.frequency_code     AS frequency_code,
    cmp.kind             AS component_kind,
    cmp.pay_component    AS pay_component,
    cmp.paycomp_value    AS paycomp_value,      -- encrypted (caja negra)
    cmp.currency         AS currency
FROM components cmp
LEFT JOIN header h ON h.user_id = cmp.user_id AND h.rn = 1
ORDER BY cmp.user_id, cmp.kind, cmp.pay_component
$seed$, $seed$Componentes de compensación por empleado (cabecera + recurrentes + no recurrentes). El importe (paycomp_value) llega encrypted desde bronze: se conserva como caja negra y NO es agregable.$seed$, $seed${}$seed$::jsonb, $seed$$seed$, NOW(), (SELECT id FROM workspaces ORDER BY created_at ASC LIMIT 1)),
($seed$sap_successfactors_empcompensation_latest$seed$, $seed$silver$seed$, $seed$sap_successfactors$seed$, $seed$["raw/sap_successfactors/EmpCompensation"]$seed$::jsonb, $seed$
-- sap_successfactors_empcompensation_latest  (silver)  cartridge: sap_successfactors
-- sources: ["raw/sap_successfactors/EmpCompensation"]
-- description: Última extracción de EmpCompensation (cabecera de compensación: grupo de pago, frecuencia). userId plano.

-- NOTA: EmpCompensation no declara select_fields; campos SF estándar de cabecera.
WITH raw AS (
    SELECT *
    FROM read_parquet('s3://{bucket}/raw/sap_successfactors/EmpCompensation/**/*.parquet',
                      hive_partitioning = true,
                      union_by_name   = true)
),
latest AS (
    SELECT *
    FROM (
        SELECT
            raw.*,
            ROW_NUMBER() OVER (
                PARTITION BY userId, startDate
                ORDER BY
                    TRY_CAST(lastModifiedDateTime AS TIMESTAMP) DESC NULLS LAST,
                    TRY_CAST(_extracted_at AS TIMESTAMP) DESC NULLS LAST,
                    TRY_CAST(load_date AS DATE) DESC NULLS LAST,
                    CAST(batch_id AS VARCHAR) DESC NULLS LAST
            ) AS _rn
        FROM raw
        WHERE userId IS NOT NULL
    )
    WHERE _rn = 1
)
SELECT
    userId               AS user_id,            -- plano
    CAST(
        COALESCE(
            TRY_CAST(startDate AS TIMESTAMP),
            to_timestamp(
                TRY_CAST(regexp_extract(CAST(startDate AS VARCHAR), '^/Date\((-?[0-9]+)', 1) AS BIGINT) / 1000
            )
        ) AS DATE
    ) AS start_date,
    payGroup             AS pay_group,
    CAST(NULL AS VARCHAR) AS frequency_code,
    load_date
FROM latest
ORDER BY user_id, start_date
$seed$, $seed$Última extracción de EmpCompensation (cabecera de compensación: grupo de pago, frecuencia). userId plano.$seed$, $seed${}$seed$::jsonb, $seed$$seed$, NOW(), (SELECT id FROM workspaces ORDER BY created_at ASC LIMIT 1)),
($seed$sap_successfactors_empemployment_latest$seed$, $seed$silver$seed$, $seed$sap_successfactors$seed$, $seed$["raw/sap_successfactors/EmpEmployment"]$seed$::jsonb, $seed$
-- sap_successfactors_empemployment_latest  (silver)  cartridge: sap_successfactors
-- sources: ["raw/sap_successfactors/EmpEmployment"]
-- description: Última extracción de EmpEmployment (relación laboral). Puente entre userId y personIdExternal (ambos planos).

WITH raw AS (
    SELECT *
    FROM read_parquet('s3://{bucket}/raw/sap_successfactors/EmpEmployment/**/*.parquet',
                      hive_partitioning = true,
                      union_by_name   = true)
),
typed AS (
    SELECT
        raw.*,
        CASE
            WHEN regexp_extract(startDate, '(-?[0-9]+)', 1) <> ''
            THEN CAST(to_timestamp(CAST(regexp_extract(startDate, '(-?[0-9]+)', 1) AS DOUBLE) / 1000) AS DATE)
            ELSE TRY_CAST(startDate AS DATE)
        END AS parsed_start_date,
        CASE
            WHEN regexp_extract(endDate, '(-?[0-9]+)', 1) <> ''
            THEN CAST(to_timestamp(CAST(regexp_extract(endDate, '(-?[0-9]+)', 1) AS DOUBLE) / 1000) AS DATE)
            ELSE TRY_CAST(endDate AS DATE)
        END AS parsed_end_date,
        CASE
            WHEN regexp_extract(originalStartDate, '(-?[0-9]+)', 1) <> ''
            THEN CAST(to_timestamp(CAST(regexp_extract(originalStartDate, '(-?[0-9]+)', 1) AS DOUBLE) / 1000) AS DATE)
            ELSE TRY_CAST(originalStartDate AS DATE)
        END AS parsed_original_start_date,
        CASE
            WHEN regexp_extract(lastModifiedDateTime, '(-?[0-9]+)', 1) <> ''
            THEN to_timestamp(CAST(regexp_extract(lastModifiedDateTime, '(-?[0-9]+)', 1) AS DOUBLE) / 1000)
            ELSE TRY_CAST(lastModifiedDateTime AS TIMESTAMP)
        END AS parsed_last_modified_at
    FROM raw
),
latest AS (
    SELECT *
    FROM (
        SELECT
            typed.*,
            ROW_NUMBER() OVER (
                PARTITION BY personIdExternal, userId, startDate
                ORDER BY
                    parsed_last_modified_at DESC NULLS LAST,
                    TRY_CAST(_extracted_at AS TIMESTAMP) DESC NULLS LAST,
                    TRY_CAST(load_date AS DATE) DESC NULLS LAST,
                    CAST(batch_id AS VARCHAR) DESC NULLS LAST
            ) AS _rn
        FROM typed
        WHERE userId IS NOT NULL
    )
    WHERE _rn = 1
)
SELECT
    personIdExternal     AS person_id_external,   -- plano
    userId               AS user_id,              -- plano
    parsed_start_date    AS start_date,
    parsed_end_date      AS end_date,
    assignmentClass      AS employee_class,
    parsed_original_start_date AS original_start_date,
    load_date
FROM latest
ORDER BY user_id, start_date
$seed$, $seed$Última extracción de EmpEmployment (relación laboral). Puente entre userId y personIdExternal (ambos planos).$seed$, $seed${}$seed$::jsonb, $seed$$seed$, NOW(), (SELECT id FROM workspaces ORDER BY created_at ASC LIMIT 1)),
($seed$sap_successfactors_empemploymenttermination_latest$seed$, $seed$silver$seed$, $seed$sap_successfactors$seed$, $seed$["raw/sap_successfactors/EmpEmploymentTermination"]$seed$::jsonb, $seed$
-- sap_successfactors_empemploymenttermination_latest  (silver)  cartridge: sap_successfactors
-- sources: ["raw/sap_successfactors/EmpEmploymentTermination"]
-- description: Última extracción de bajas (EmpEmploymentTermination). userId plano; la entidad está registrada para extracción.

-- Dedupe por empleado + fecha para conservar una baja real por evento
-- y descartar snapshots repetidos de Bronze.
WITH raw AS (
    SELECT *
    FROM read_parquet('s3://{bucket}/raw/sap_successfactors/EmpEmploymentTermination/**/*.parquet',
                      hive_partitioning = true,
                      union_by_name   = true)
),
normalized AS (
    SELECT
        raw.*,
        COALESCE(
            TRY_CAST(endDate AS DATE),
            CAST(
                to_timestamp(
                    TRY_CAST(regexp_extract(CAST(endDate AS VARCHAR), '^/Date\((-?[0-9]+)', 1) AS DOUBLE) / 1000
                ) AS DATE
            )
        ) AS _termination_date,
        COALESCE(
            TRY_CAST(lastModifiedDateTime AS TIMESTAMP),
            to_timestamp(
                TRY_CAST(regexp_extract(CAST(lastModifiedDateTime AS VARCHAR), '^/Date\((-?[0-9]+)', 1) AS DOUBLE) / 1000
            )
        ) AS _last_modified_at
    FROM raw
),
latest AS (
    SELECT *
    FROM (
        SELECT
            normalized.*,
            ROW_NUMBER() OVER (
                PARTITION BY userId, endDate
                ORDER BY
                    _last_modified_at DESC NULLS LAST,
                    TRY_CAST(_extracted_at AS TIMESTAMP) DESC NULLS LAST,
                    TRY_CAST(load_date AS DATE) DESC NULLS LAST,
                    CAST(batch_id AS VARCHAR) DESC NULLS LAST
            ) AS _rn
        FROM normalized
        WHERE userId IS NOT NULL
    )
    WHERE _rn = 1
)
SELECT
    userId                     AS user_id,            -- plano
    _termination_date          AS termination_date,
    CAST(NULL AS VARCHAR)      AS event_reason,       -- no visible por permisos OData en este tenant
    load_date
FROM latest
ORDER BY user_id, termination_date
$seed$, $seed$Última extracción de bajas (EmpEmploymentTermination). userId plano; la entidad está registrada para extracción.$seed$, $seed${}$seed$::jsonb, $seed$$seed$, NOW(), (SELECT id FROM workspaces ORDER BY created_at ASC LIMIT 1)),
($seed$sap_successfactors_empjob_latest$seed$, $seed$silver$seed$, $seed$sap_successfactors$seed$, $seed$["raw/sap_successfactors/EmpJob"]$seed$::jsonb, $seed$
-- sap_successfactors_empjob_latest  (silver)  cartridge: sap_successfactors
-- sources: ["raw/sap_successfactors/EmpJob"]
-- description: Última extracción de EmpJob (asignación de puesto efectivo-fechada). userId y managerId planos; códigos de org casan con externalCode de los FO.

WITH raw AS (
    SELECT *
    FROM read_parquet('s3://{bucket}/raw/sap_successfactors/EmpJob/**/*.parquet',
                      hive_partitioning = true,
                      union_by_name   = true)
),
latest AS (
    SELECT *
    FROM (
        SELECT
            raw.*,
            ROW_NUMBER() OVER (
                PARTITION BY userId, startDate
                ORDER BY
                    TRY_CAST(lastModifiedDateTime AS TIMESTAMP) DESC NULLS LAST,
                    TRY_CAST(_extracted_at AS TIMESTAMP) DESC NULLS LAST,
                    TRY_CAST(load_date AS DATE) DESC NULLS LAST,
                    CAST(batch_id AS VARCHAR) DESC NULLS LAST
            ) AS _rn
        FROM raw
        WHERE userId IS NOT NULL
    )
    WHERE _rn = 1
)
SELECT
    userId               AS user_id,              -- plano
    TRY_CAST(startDate AS DATE) AS start_date,
    TRY_CAST(endDate AS DATE)   AS end_date,
    jobCode              AS job_code,
    position             AS position,
    department           AS department,
    division             AS division,
    location             AS location,
    businessUnit         AS business_unit,
    company              AS company,
    costCenter           AS cost_center,
    managerId            AS manager_id,           -- plano (habilita manager_hierarchy real)
    eventReason          AS event_reason,
    load_date
FROM latest
ORDER BY user_id, start_date
$seed$, $seed$Última extracción de EmpJob (asignación de puesto efectivo-fechada). userId y managerId planos; códigos de org casan con externalCode de los FO.$seed$, $seed${}$seed$::jsonb, $seed$$seed$, NOW(), (SELECT id FROM workspaces ORDER BY created_at ASC LIMIT 1)),
($seed$sap_successfactors_employee_360$seed$, $seed$gold$seed$, $seed$sap_successfactors$seed$, $seed$["silver/sap_successfactors/sap_successfactors_empemployment_latest", "silver/sap_successfactors/sap_successfactors_empjob_latest", "silver/sap_successfactors/sap_successfactors_perpersonal_latest", "silver/sap_successfactors/sap_successfactors_focompany_latest", "silver/sap_successfactors/sap_successfactors_fodepartment_latest", "silver/sap_successfactors/sap_successfactors_fodivision_latest", "silver/sap_successfactors/sap_successfactors_folocation_latest"]$seed$::jsonb, $seed$
-- sap_successfactors_employee_360  (gold)  cartridge: sap_successfactors
-- sources: ["silver/sap_successfactors/sap_successfactors_empemployment_latest", "silver/sap_successfactors/sap_successfactors_empjob_latest", "silver/sap_successfactors/sap_successfactors_perpersonal_latest", "silver/sap_successfactors/sap_successfactors_focompany_latest", "silver/sap_successfactors/sap_successfactors_fodepartment_latest", "silver/sap_successfactors/sap_successfactors_fodivision_latest", "silver/sap_successfactors/sap_successfactors_folocation_latest"]
-- description: Vista 360 del empleado activo: empleo + puesto + nombre (PerPersonal) + nombres de org. Una fila por empleado.

-- NOTA de privacidad: se une por claves PLANAS (EmpEmployment/EmpJob.user_id y
-- EmpEmployment.person_id_external). User y PerPerson quedan FUERA porque su
-- clave está shadowed (hash) y no casa con las planas — artefacto del Bloque A.
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
    CASE WHEN e.start_date <= CURRENT_DATE AND e.end_date >= CURRENT_DATE
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
$seed$, $seed$Vista 360 del empleado activo: empleo + puesto + nombre (PerPersonal) + nombres de org. Una fila por empleado.$seed$, $seed${}$seed$::jsonb, $seed$$seed$, NOW(), (SELECT id FROM workspaces ORDER BY created_at ASC LIMIT 1)),
($seed$sap_successfactors_employees_anomalies$seed$, $seed$gold$seed$, $seed$sap_successfactors$seed$, $seed$["raw/sap_successfactors/EmpEmployment", "raw/sap_successfactors/EmpJob", "raw/sap_successfactors/PerPersonal", "raw/sap_successfactors/FOJobCode"]$seed$::jsonb, $seed$
-- sap_successfactors_employees_anomalies  (gold)  cartridge: sap_successfactors
-- sources: ["raw/sap_successfactors/EmpEmployment", "raw/sap_successfactors/EmpJob", "raw/sap_successfactors/PerPersonal", "raw/sap_successfactors/FOJobCode"]
-- description: Detección automática de irregularidades en empleados activos (preparación Fase 3). UNION de varios casos con tipo, severidad y detalle.

-- Mejora propia (estilo HCM/S4). Casos hoy: sin departamento, sin manager,
-- job_code inexistente en FOJobCode. Pendiente (requiere extracción de
-- EmpEmploymentTermination activa cruzada): activo con baja registrada.
WITH emp AS (
    SELECT user_id, full_name, department_id, manager_id, job_code
    FROM read_parquet('s3://{bucket}/gold/sap_successfactors/sap_successfactors_employee_360/**/*.parquet')
    WHERE is_active = TRUE
),
job_codes AS (
    SELECT job_code FROM read_parquet('s3://{bucket}/silver/sap_successfactors/sap_successfactors_fojobcode_latest/**/*.parquet')
),
no_department AS (
    SELECT user_id, full_name,
           'missing_department' AS anomaly_type, 'medium' AS severity,
           '{"reason":"empleado activo sin departamento"}' AS details
    FROM emp WHERE department_id IS NULL OR TRIM(department_id) = ''
),
no_manager AS (
    SELECT user_id, full_name,
           'missing_manager' AS anomaly_type, 'low' AS severity,
           '{"reason":"empleado activo sin manager (revisar si es C-level)"}' AS details
    FROM emp WHERE manager_id IS NULL OR TRIM(manager_id) = ''
),
invalid_jobcode AS (
    SELECT e.user_id, e.full_name,
           'invalid_job_code' AS anomaly_type, 'high' AS severity,
           '{"reason":"job_code no existe en FOJobCode"}' AS details
    FROM emp e
    WHERE e.job_code IS NOT NULL
      AND e.job_code NOT IN (SELECT job_code FROM job_codes WHERE job_code IS NOT NULL)
)
SELECT user_id, full_name, anomaly_type, severity, details,
       CURRENT_TIMESTAMP AS detected_at
FROM (
    SELECT * FROM no_department
    UNION ALL
    SELECT * FROM no_manager
    UNION ALL
    SELECT * FROM invalid_jobcode
) anomalies
ORDER BY severity, anomaly_type, user_id
$seed$, $seed$Detección automática de irregularidades en empleados activos (preparación Fase 3). UNION de varios casos con tipo, severidad y detalle.$seed$, $seed${}$seed$::jsonb, $seed$$seed$, NOW(), (SELECT id FROM workspaces ORDER BY created_at ASC LIMIT 1)),
($seed$sap_successfactors_emppaycompnonrecurring_latest$seed$, $seed$silver$seed$, $seed$sap_successfactors$seed$, $seed$["raw/sap_successfactors/EmpPayCompNonRecurring"]$seed$::jsonb, $seed$
-- sap_successfactors_emppaycompnonrecurring_latest  (silver)  cartridge: sap_successfactors
-- sources: ["raw/sap_successfactors/EmpPayCompNonRecurring"]
-- description: Última extracción de pagos no recurrentes (bonos, pagos únicos). paycompValue encrypted desde bronze (caja negra; NO agregable).

WITH raw AS (
    SELECT *
    FROM read_parquet('s3://{bucket}/raw/sap_successfactors/EmpPayCompNonRecurring/**/*.parquet',
                      hive_partitioning = true,
                      union_by_name   = true)
),
normalized AS (
    SELECT
        raw.*,
        COALESCE(
            TRY_CAST(payDate AS DATE),
            CAST(
                to_timestamp(
                    TRY_CAST(regexp_extract(CAST(payDate AS VARCHAR), '^/Date\((-?[0-9]+)', 1) AS DOUBLE) / 1000
                ) AS DATE
            )
        ) AS _pay_date,
        COALESCE(
            TRY_CAST(lastModifiedDateTime AS TIMESTAMP),
            to_timestamp(
                TRY_CAST(regexp_extract(CAST(lastModifiedDateTime AS VARCHAR), '^/Date\((-?[0-9]+)', 1) AS DOUBLE) / 1000
            )
        ) AS _last_modified_at
    FROM raw
),
latest AS (
    SELECT *
    FROM (
        SELECT
            normalized.*,
            ROW_NUMBER() OVER (
                PARTITION BY userId, payComponentCode, payDate
                ORDER BY
                    _last_modified_at DESC NULLS LAST,
                    TRY_CAST(_extracted_at AS TIMESTAMP) DESC NULLS LAST,
                    TRY_CAST(load_date AS DATE) DESC NULLS LAST,
                    CAST(batch_id AS VARCHAR) DESC NULLS LAST
            ) AS _rn
        FROM normalized
        WHERE userId IS NOT NULL
    )
    WHERE _rn = 1
)
SELECT
    userId               AS user_id,            -- plano
    payComponentCode     AS pay_component,
    value                AS paycomp_value,      -- encrypted en bronze (no agregable)
    currencyCode         AS currency,
    _pay_date            AS pay_date,
    load_date
FROM latest
ORDER BY user_id, pay_date
$seed$, $seed$Última extracción de pagos no recurrentes (bonos, pagos únicos). paycompValue encrypted desde bronze (caja negra; NO agregable).$seed$, $seed${}$seed$::jsonb, $seed$$seed$, NOW(), (SELECT id FROM workspaces ORDER BY created_at ASC LIMIT 1)),
($seed$sap_successfactors_emppaycomprecurring_latest$seed$, $seed$silver$seed$, $seed$sap_successfactors$seed$, $seed$["raw/sap_successfactors/EmpPayCompRecurring"]$seed$::jsonb, $seed$
-- sap_successfactors_emppaycomprecurring_latest  (silver)  cartridge: sap_successfactors
-- sources: ["raw/sap_successfactors/EmpPayCompRecurring"]
-- description: Última extracción de pagos recurrentes (salario base, complementos). paycompValue llega encrypted desde bronze (caja negra; NO agregable en SQL).

WITH raw AS (
    SELECT *
    FROM read_parquet('s3://{bucket}/raw/sap_successfactors/EmpPayCompRecurring/**/*.parquet',
                      hive_partitioning = true,
                      union_by_name   = true)
),
normalized AS (
    SELECT
        raw.*,
        COALESCE(
            TRY_CAST(startDate AS DATE),
            CAST(
                to_timestamp(
                    TRY_CAST(regexp_extract(CAST(startDate AS VARCHAR), '^/Date\((-?[0-9]+)', 1) AS DOUBLE) / 1000
                ) AS DATE
            )
        ) AS _start_date,
        COALESCE(
            TRY_CAST(lastModifiedDateTime AS TIMESTAMP),
            to_timestamp(
                TRY_CAST(regexp_extract(CAST(lastModifiedDateTime AS VARCHAR), '^/Date\((-?[0-9]+)', 1) AS DOUBLE) / 1000
            )
        ) AS _last_modified_at
    FROM raw
),
latest AS (
    SELECT *
    FROM (
        SELECT
            normalized.*,
            ROW_NUMBER() OVER (
                PARTITION BY userId, payComponent, startDate
                ORDER BY
                    _last_modified_at DESC NULLS LAST,
                    TRY_CAST(_extracted_at AS TIMESTAMP) DESC NULLS LAST,
                    TRY_CAST(load_date AS DATE) DESC NULLS LAST,
                    CAST(batch_id AS VARCHAR) DESC NULLS LAST
            ) AS _rn
        FROM normalized
        WHERE userId IS NOT NULL
    )
    WHERE _rn = 1
)
SELECT
    userId               AS user_id,            -- plano
    payComponent         AS pay_component,
    paycompvalue         AS paycomp_value,      -- encrypted en bronze (no agregable)
    frequency            AS frequency,
    currencyCode         AS currency,
    _start_date          AS start_date,
    load_date
FROM latest
ORDER BY user_id, pay_component, start_date
$seed$, $seed$Última extracción de pagos recurrentes (salario base, complementos). paycompValue llega encrypted desde bronze (caja negra; NO agregable en SQL).$seed$, $seed${}$seed$::jsonb, $seed$$seed$, NOW(), (SELECT id FROM workspaces ORDER BY created_at ASC LIMIT 1)),
($seed$sap_successfactors_fobusinessunit_latest$seed$, $seed$silver$seed$, $seed$sap_successfactors$seed$, $seed$["raw/sap_successfactors/FOBusinessUnit"]$seed$::jsonb, $seed$
-- sap_successfactors_fobusinessunit_latest  (silver)  cartridge: sap_successfactors
-- sources: ["raw/sap_successfactors/FOBusinessUnit"]
-- description: Última extracción del objeto de fundación Unidad de Negocio (FOBusinessUnit).

WITH raw AS (
    SELECT *
    FROM read_parquet('s3://{bucket}/raw/sap_successfactors/FOBusinessUnit/**/*.parquet',
                      hive_partitioning = true,
                      union_by_name   = true)
),
latest AS (
    SELECT *
    FROM (
        SELECT
            raw.*,
            ROW_NUMBER() OVER (
                PARTITION BY externalCode
                ORDER BY
                    TRY_CAST(lastModifiedDateTime AS TIMESTAMP) DESC NULLS LAST,
                    TRY_CAST(_extracted_at AS TIMESTAMP) DESC NULLS LAST,
                    TRY_CAST(load_date AS DATE) DESC NULLS LAST,
                    CAST(batch_id AS VARCHAR) DESC NULLS LAST
            ) AS _rn
        FROM raw
        WHERE externalCode IS NOT NULL
    )
    WHERE _rn = 1
)
SELECT
    externalCode         AS business_unit_id,
    name_defaultValue    AS business_unit_name,
    load_date
FROM latest
ORDER BY business_unit_id
$seed$, $seed$Última extracción del objeto de fundación Unidad de Negocio (FOBusinessUnit).$seed$, $seed${}$seed$::jsonb, $seed$$seed$, NOW(), (SELECT id FROM workspaces ORDER BY created_at ASC LIMIT 1)),
($seed$sap_successfactors_focompany_latest$seed$, $seed$silver$seed$, $seed$sap_successfactors$seed$, $seed$["raw/sap_successfactors/FOCompany"]$seed$::jsonb, $seed$
-- sap_successfactors_focompany_latest  (silver)  cartridge: sap_successfactors
-- sources: ["raw/sap_successfactors/FOCompany"]
-- description: Última extracción del objeto de fundación Compañía (FOCompany).

-- externalCode es plano (casa con EmpJob). Dedupe por clave de negocio para
-- que _latest no acumule snapshots de pruebas o corridas diarias.
WITH raw AS (
    SELECT *
    FROM read_parquet('s3://{bucket}/raw/sap_successfactors/FOCompany/**/*.parquet',
                      hive_partitioning = true,
                      union_by_name   = true)
),
latest AS (
    SELECT *
    FROM (
        SELECT
            raw.*,
            ROW_NUMBER() OVER (
                PARTITION BY externalCode
                ORDER BY
                    TRY_CAST(lastModifiedDateTime AS TIMESTAMP) DESC NULLS LAST,
                    TRY_CAST(_extracted_at AS TIMESTAMP) DESC NULLS LAST,
                    TRY_CAST(load_date AS DATE) DESC NULLS LAST,
                    CAST(batch_id AS VARCHAR) DESC NULLS LAST
            ) AS _rn
        FROM raw
        WHERE externalCode IS NOT NULL
    )
    WHERE _rn = 1
)
SELECT
    externalCode         AS company_id,
    name_defaultValue    AS company_name,
    country              AS country,
    load_date
FROM latest
ORDER BY company_id
$seed$, $seed$Última extracción del objeto de fundación Compañía (FOCompany).$seed$, $seed${}$seed$::jsonb, $seed$$seed$, NOW(), (SELECT id FROM workspaces ORDER BY created_at ASC LIMIT 1)),
($seed$sap_successfactors_fodepartment_latest$seed$, $seed$silver$seed$, $seed$sap_successfactors$seed$, $seed$["raw/sap_successfactors/FODepartment"]$seed$::jsonb, $seed$
-- sap_successfactors_fodepartment_latest  (silver)  cartridge: sap_successfactors
-- sources: ["raw/sap_successfactors/FODepartment"]
-- description: Última extracción del objeto de fundación Departamento (FODepartment).

WITH raw AS (
    SELECT *
    FROM read_parquet('s3://{bucket}/raw/sap_successfactors/FODepartment/**/*.parquet',
                      hive_partitioning = true,
                      union_by_name   = true)
),
latest AS (
    SELECT *
    FROM (
        SELECT
            raw.*,
            ROW_NUMBER() OVER (
                PARTITION BY externalCode
                ORDER BY
                    TRY_CAST(lastModifiedDateTime AS TIMESTAMP) DESC NULLS LAST,
                    TRY_CAST(_extracted_at AS TIMESTAMP) DESC NULLS LAST,
                    TRY_CAST(load_date AS DATE) DESC NULLS LAST,
                    CAST(batch_id AS VARCHAR) DESC NULLS LAST
            ) AS _rn
        FROM raw
        WHERE externalCode IS NOT NULL
    )
    WHERE _rn = 1
)
SELECT
    externalCode         AS department_id,
    name_defaultValue    AS department_name,
    costCenter           AS cost_center,
    load_date
FROM latest
ORDER BY department_id
$seed$, $seed$Última extracción del objeto de fundación Departamento (FODepartment).$seed$, $seed${}$seed$::jsonb, $seed$$seed$, NOW(), (SELECT id FROM workspaces ORDER BY created_at ASC LIMIT 1)),
($seed$sap_successfactors_fodivision_latest$seed$, $seed$silver$seed$, $seed$sap_successfactors$seed$, $seed$["raw/sap_successfactors/FODivision"]$seed$::jsonb, $seed$
-- sap_successfactors_fodivision_latest  (silver)  cartridge: sap_successfactors
-- sources: ["raw/sap_successfactors/FODivision"]
-- description: Última extracción del objeto de fundación División (FODivision).

WITH raw AS (
    SELECT *
    FROM read_parquet('s3://{bucket}/raw/sap_successfactors/FODivision/**/*.parquet',
                      hive_partitioning = true,
                      union_by_name   = true)
),
latest AS (
    SELECT *
    FROM (
        SELECT
            raw.*,
            ROW_NUMBER() OVER (
                PARTITION BY externalCode
                ORDER BY
                    TRY_CAST(lastModifiedDateTime AS TIMESTAMP) DESC NULLS LAST,
                    TRY_CAST(_extracted_at AS TIMESTAMP) DESC NULLS LAST,
                    TRY_CAST(load_date AS DATE) DESC NULLS LAST,
                    CAST(batch_id AS VARCHAR) DESC NULLS LAST
            ) AS _rn
        FROM raw
        WHERE externalCode IS NOT NULL
    )
    WHERE _rn = 1
)
SELECT
    externalCode         AS division_id,
    name_defaultValue    AS division_name,
    load_date
FROM latest
ORDER BY division_id
$seed$, $seed$Última extracción del objeto de fundación División (FODivision).$seed$, $seed${}$seed$::jsonb, $seed$$seed$, NOW(), (SELECT id FROM workspaces ORDER BY created_at ASC LIMIT 1)),
($seed$sap_successfactors_fojobcode_latest$seed$, $seed$silver$seed$, $seed$sap_successfactors$seed$, $seed$["raw/sap_successfactors/FOJobCode"]$seed$::jsonb, $seed$
-- sap_successfactors_fojobcode_latest  (silver)  cartridge: sap_successfactors
-- sources: ["raw/sap_successfactors/FOJobCode"]
-- description: Última extracción del objeto de fundación Código de Puesto (FOJobCode).

WITH raw AS (
    SELECT *
    FROM read_parquet('s3://{bucket}/raw/sap_successfactors/FOJobCode/**/*.parquet',
                      hive_partitioning = true,
                      union_by_name   = true)
),
latest AS (
    SELECT *
    FROM (
        SELECT
            raw.*,
            ROW_NUMBER() OVER (
                PARTITION BY externalCode
                ORDER BY
                    TRY_CAST(lastModifiedDateTime AS TIMESTAMP) DESC NULLS LAST,
                    TRY_CAST(_extracted_at AS TIMESTAMP) DESC NULLS LAST,
                    TRY_CAST(load_date AS DATE) DESC NULLS LAST,
                    CAST(batch_id AS VARCHAR) DESC NULLS LAST
            ) AS _rn
        FROM raw
        WHERE externalCode IS NOT NULL
    )
    WHERE _rn = 1
)
SELECT
    externalCode         AS job_code,
    name_defaultValue    AS job_name,
    load_date
FROM latest
ORDER BY job_code
$seed$, $seed$Última extracción del objeto de fundación Código de Puesto (FOJobCode).$seed$, $seed${}$seed$::jsonb, $seed$$seed$, NOW(), (SELECT id FROM workspaces ORDER BY created_at ASC LIMIT 1)),
($seed$sap_successfactors_folocation_latest$seed$, $seed$silver$seed$, $seed$sap_successfactors$seed$, $seed$["raw/sap_successfactors/FOLocation"]$seed$::jsonb, $seed$
-- sap_successfactors_folocation_latest  (silver)  cartridge: sap_successfactors
-- sources: ["raw/sap_successfactors/FOLocation"]
-- description: Última extracción del objeto de fundación Ubicación (FOLocation).

WITH raw AS (
    SELECT *
    FROM read_parquet('s3://{bucket}/raw/sap_successfactors/FOLocation/**/*.parquet',
                      hive_partitioning = true,
                      union_by_name   = true)
),
latest AS (
    SELECT *
    FROM (
        SELECT
            raw.*,
            ROW_NUMBER() OVER (
                PARTITION BY externalCode
                ORDER BY
                    TRY_CAST(lastModifiedDateTime AS TIMESTAMP) DESC NULLS LAST,
                    TRY_CAST(_extracted_at AS TIMESTAMP) DESC NULLS LAST,
                    TRY_CAST(load_date AS DATE) DESC NULLS LAST,
                    CAST(batch_id AS VARCHAR) DESC NULLS LAST
            ) AS _rn
        FROM raw
        WHERE externalCode IS NOT NULL
    )
    WHERE _rn = 1
)
SELECT
    externalCode         AS location_id,
    name                 AS location_name,
    status               AS status,
    TRY_CAST(startDate AS DATE) AS valid_from,
    TRY_CAST(endDate AS DATE)   AS valid_to,
    load_date
FROM latest
ORDER BY location_id
$seed$, $seed$Última extracción del objeto de fundación Ubicación (FOLocation).$seed$, $seed${}$seed$::jsonb, $seed$$seed$, NOW(), (SELECT id FROM workspaces ORDER BY created_at ASC LIMIT 1)),
($seed$sap_successfactors_headcount_by_company$seed$, $seed$gold$seed$, $seed$sap_successfactors$seed$, $seed$["raw/sap_successfactors/EmpEmployment", "raw/sap_successfactors/EmpJob", "raw/sap_successfactors/FOCompany"]$seed$::jsonb, $seed$
-- sap_successfactors_headcount_by_company  (gold)  cartridge: sap_successfactors
-- sources: ["raw/sap_successfactors/EmpEmployment", "raw/sap_successfactors/EmpJob", "raw/sap_successfactors/FOCompany"]
-- description: Empleados activos por compañía legal (snapshot del mes en curso).

WITH emp AS (
    SELECT company_id, company_name
    FROM read_parquet('s3://{bucket}/gold/sap_successfactors/sap_successfactors_employee_360/**/*.parquet')
    WHERE is_active = TRUE
)
SELECT
    COALESCE(company_id, '(sin compania)')          AS company_id,
    COALESCE(company_name, '(sin nombre)')          AS company_name,
    COUNT(*)                                        AS headcount,
    CAST(DATE_TRUNC('month', CURRENT_DATE) AS DATE) AS snapshot_month
FROM emp
GROUP BY company_id, company_name
ORDER BY headcount DESC, company_id
$seed$, $seed$Empleados activos por compañía legal (snapshot del mes en curso).$seed$, $seed${}$seed$::jsonb, $seed$$seed$, NOW(), (SELECT id FROM workspaces ORDER BY created_at ASC LIMIT 1)),
($seed$sap_successfactors_headcount_by_department$seed$, $seed$gold$seed$, $seed$sap_successfactors$seed$, $seed$["raw/sap_successfactors/EmpEmployment", "raw/sap_successfactors/EmpJob", "raw/sap_successfactors/FODepartment"]$seed$::jsonb, $seed$
-- sap_successfactors_headcount_by_department  (gold)  cartridge: sap_successfactors
-- sources: ["raw/sap_successfactors/EmpEmployment", "raw/sap_successfactors/EmpJob", "raw/sap_successfactors/FODepartment"]
-- description: Empleados activos por departamento (snapshot del mes en curso).

-- Nombre prefijado con el cartucho: datasets.name es PK global y headcount_by_department
-- ya existe para sap_hcm.
WITH emp AS (
    SELECT department_id, department_name
    FROM read_parquet('s3://{bucket}/gold/sap_successfactors/sap_successfactors_employee_360/**/*.parquet')
    WHERE is_active = TRUE
)
SELECT
    COALESCE(department_id, '(sin departamento)')   AS department_id,
    COALESCE(department_name, '(sin nombre)')       AS department_name,
    COUNT(*)                                        AS headcount,
    CAST(DATE_TRUNC('month', CURRENT_DATE) AS DATE) AS snapshot_month
FROM emp
GROUP BY department_id, department_name
ORDER BY headcount DESC, department_id
$seed$, $seed$Empleados activos por departamento (snapshot del mes en curso).$seed$, $seed${}$seed$::jsonb, $seed$$seed$, NOW(), (SELECT id FROM workspaces ORDER BY created_at ASC LIMIT 1)),
($seed$sap_successfactors_headcount_by_location$seed$, $seed$gold$seed$, $seed$sap_successfactors$seed$, $seed$["raw/sap_successfactors/EmpEmployment", "raw/sap_successfactors/EmpJob", "raw/sap_successfactors/FOLocation"]$seed$::jsonb, $seed$
-- sap_successfactors_headcount_by_location  (gold)  cartridge: sap_successfactors
-- sources: ["raw/sap_successfactors/EmpEmployment", "raw/sap_successfactors/EmpJob", "raw/sap_successfactors/FOLocation"]
-- description: Empleados activos por ubicación (snapshot del mes en curso).

WITH emp AS (
    SELECT location_id, location_name
    FROM read_parquet('s3://{bucket}/gold/sap_successfactors/sap_successfactors_employee_360/**/*.parquet')
    WHERE is_active = TRUE
)
SELECT
    COALESCE(location_id, '(sin ubicacion)')        AS location_id,
    COALESCE(location_name, '(sin nombre)')         AS location_name,
    COUNT(*)                                        AS headcount,
    CAST(DATE_TRUNC('month', CURRENT_DATE) AS DATE) AS snapshot_month
FROM emp
GROUP BY location_id, location_name
ORDER BY headcount DESC, location_id
$seed$, $seed$Empleados activos por ubicación (snapshot del mes en curso).$seed$, $seed${}$seed$::jsonb, $seed$$seed$, NOW(), (SELECT id FROM workspaces ORDER BY created_at ASC LIMIT 1)),
($seed$sap_successfactors_jobrequisition_latest$seed$, $seed$silver$seed$, $seed$sap_successfactors$seed$, $seed$["raw/sap_successfactors/JobRequisition"]$seed$::jsonb, $seed$
-- sap_successfactors_jobrequisition_latest  (silver)  cartridge: sap_successfactors
-- sources: ["raw/sap_successfactors/JobRequisition"]
-- description: Última extracción de requisiciones de empleo (Recruiting).

WITH raw AS (
    SELECT *
    FROM read_parquet('s3://{bucket}/raw/sap_successfactors/JobRequisition/**/*.parquet',
                      hive_partitioning = true,
                      union_by_name   = true)
),
latest AS (
    SELECT *
    FROM (
        SELECT
            raw.*,
            ROW_NUMBER() OVER (
                PARTITION BY jobReqId
                ORDER BY
                    TRY_CAST(lastModifiedDateTime AS TIMESTAMP) DESC NULLS LAST,
                    TRY_CAST(_extracted_at AS TIMESTAMP) DESC NULLS LAST,
                    TRY_CAST(load_date AS DATE) DESC NULLS LAST,
                    CAST(batch_id AS VARCHAR) DESC NULLS LAST
            ) AS _rn
        FROM raw
        WHERE jobReqId IS NOT NULL
    )
    WHERE _rn = 1
)
SELECT
    jobReqId             AS job_req_id,
    jobTitle             AS job_title,
    status               AS status,
    department           AS department,
    location             AS location,
    load_date
FROM latest
ORDER BY job_req_id
$seed$, $seed$Última extracción de requisiciones de empleo (Recruiting).$seed$, $seed${}$seed$::jsonb, $seed$$seed$, NOW(), (SELECT id FROM workspaces ORDER BY created_at ASC LIMIT 1)),
($seed$sap_successfactors_manager_hierarchy$seed$, $seed$gold$seed$, $seed$sap_successfactors$seed$, $seed$["raw/sap_successfactors/EmpEmployment", "raw/sap_successfactors/EmpJob", "raw/sap_successfactors/PerPersonal"]$seed$::jsonb, $seed$
-- sap_successfactors_manager_hierarchy  (gold)  cartridge: sap_successfactors
-- sources: ["raw/sap_successfactors/EmpEmployment", "raw/sap_successfactors/EmpJob", "raw/sap_successfactors/PerPersonal"]
-- description: Árbol de supervisión: cada empleado activo con su manager directo, número de reportes directos y profundidad en la jerarquía. Real gracias a EmpJob.managerId (plano).

WITH RECURSIVE emp AS (
    SELECT user_id, full_name, manager_id
    FROM read_parquet('s3://{bucket}/gold/sap_successfactors/sap_successfactors_employee_360/**/*.parquet')
    WHERE is_active = TRUE
),
reports AS (
    SELECT manager_id, COUNT(*) AS direct_reports
    FROM emp WHERE manager_id IS NOT NULL
    GROUP BY manager_id
),
hier AS (
    -- Raíces: sin manager o cuyo manager no está en la plantilla activa.
    SELECT user_id, manager_id, 0 AS depth
    FROM emp
    WHERE manager_id IS NULL OR manager_id NOT IN (SELECT user_id FROM emp)
    UNION ALL
    SELECT e.user_id, e.manager_id, h.depth + 1
    FROM emp e
    JOIN hier h ON e.manager_id = h.user_id
    WHERE h.depth < 20
),
depth_per_user AS (
    SELECT user_id, MIN(depth) AS depth FROM hier GROUP BY user_id
)
SELECT
    e.user_id                       AS user_id,            -- plano
    e.full_name                     AS full_name,          -- masked
    e.manager_id                    AS manager_id,
    COALESCE(r.direct_reports, 0)   AS direct_reports,
    COALESCE(d.depth, 0)            AS depth
FROM emp e
LEFT JOIN reports r ON r.manager_id = e.user_id
LEFT JOIN depth_per_user d ON d.user_id = e.user_id
ORDER BY depth, direct_reports DESC, e.user_id
$seed$, $seed$Árbol de supervisión: cada empleado activo con su manager directo, número de reportes directos y profundidad en la jerarquía. Real gracias a EmpJob.managerId (plano).$seed$, $seed${}$seed$::jsonb, $seed$$seed$, NOW(), (SELECT id FROM workspaces ORDER BY created_at ASC LIMIT 1)),
($seed$sap_successfactors_org_structure$seed$, $seed$gold$seed$, $seed$sap_successfactors$seed$, $seed$["silver/sap_successfactors/sap_successfactors_empjob_latest", "silver/sap_successfactors/sap_successfactors_focompany_latest", "silver/sap_successfactors/sap_successfactors_fodivision_latest", "silver/sap_successfactors/sap_successfactors_fodepartment_latest", "silver/sap_successfactors/sap_successfactors_folocation_latest", "silver/sap_successfactors/sap_successfactors_fobusinessunit_latest"]$seed$::jsonb, $seed$
-- sap_successfactors_org_structure  (gold)  cartridge: sap_successfactors
-- sources: ["silver/sap_successfactors/sap_successfactors_empjob_latest", "silver/sap_successfactors/sap_successfactors_focompany_latest", "silver/sap_successfactors/sap_successfactors_fodivision_latest", "silver/sap_successfactors/sap_successfactors_fodepartment_latest", "silver/sap_successfactors/sap_successfactors_folocation_latest", "silver/sap_successfactors/sap_successfactors_fobusinessunit_latest"]
-- description: Estructura organizacional observada: combinaciones distintas de compañía/división/departamento/ubicación según las asignaciones de EmpJob, con nombres de los FO.

-- Los maestros FO no tienen FK entre sí en la extracción plana; la estructura
-- real se deriva de las asignaciones vigentes (EmpJob) y se enriquece con nombres.
WITH combos AS (
    SELECT DISTINCT company, division, department, location, business_unit
    FROM read_parquet('s3://{bucket}/silver/sap_successfactors/sap_successfactors_empjob_latest/**/*.parquet')
),
company AS (SELECT company_id, company_name FROM read_parquet('s3://{bucket}/silver/sap_successfactors/sap_successfactors_focompany_latest/**/*.parquet')),
divi AS (SELECT division_id, division_name FROM read_parquet('s3://{bucket}/silver/sap_successfactors/sap_successfactors_fodivision_latest/**/*.parquet')),
dept AS (SELECT department_id, department_name FROM read_parquet('s3://{bucket}/silver/sap_successfactors/sap_successfactors_fodepartment_latest/**/*.parquet')),
loc AS (SELECT location_id, location_name FROM read_parquet('s3://{bucket}/silver/sap_successfactors/sap_successfactors_folocation_latest/**/*.parquet')),
bu AS (SELECT business_unit_id, business_unit_name FROM read_parquet('s3://{bucket}/silver/sap_successfactors/sap_successfactors_fobusinessunit_latest/**/*.parquet'))
SELECT
    c.company           AS company_id,
    co.company_name     AS company_name,
    c.division          AS division_id,
    dv.division_name    AS division_name,
    c.department        AS department_id,
    d.department_name   AS department_name,
    c.location          AS location_id,
    l.location_name     AS location_name,
    c.business_unit     AS business_unit_id,
    bu.business_unit_name AS business_unit_name
FROM combos c
LEFT JOIN company co ON co.company_id = c.company
LEFT JOIN divi dv ON dv.division_id = c.division
LEFT JOIN dept d ON d.department_id = c.department
LEFT JOIN loc l ON l.location_id = c.location
LEFT JOIN bu ON bu.business_unit_id = c.business_unit
ORDER BY company_id, division_id, department_id, location_id
$seed$, $seed$Estructura organizacional observada: combinaciones distintas de compañía/división/departamento/ubicación según las asignaciones de EmpJob, con nombres de los FO.$seed$, $seed${}$seed$::jsonb, $seed$$seed$, NOW(), (SELECT id FROM workspaces ORDER BY created_at ASC LIMIT 1)),
($seed$sap_successfactors_paymentinformationdetailv3_latest$seed$, $seed$silver$seed$, $seed$sap_successfactors$seed$, $seed$["raw/sap_successfactors/PaymentInformationDetailV3"]$seed$::jsonb, $seed$
-- sap_successfactors_paymentinformationdetailv3_latest  (silver)  cartridge: sap_successfactors
-- sources: ["raw/sap_successfactors/PaymentInformationDetailV3"]
-- description: Última extracción deduplicada de PaymentInformationDetailV3. Campos bancarios sensibles llegan masked/encrypted desde bronze.

WITH raw AS (
    SELECT *
    FROM read_parquet('s3://{bucket}/raw/sap_successfactors/PaymentInformationDetailV3/**/*.parquet',
                      hive_partitioning = true,
                      union_by_name   = true)
),
latest AS (
    SELECT *
    FROM (
        SELECT
            raw.*,
            ROW_NUMBER() OVER (
                PARTITION BY externalCode
                ORDER BY
                    TRY_CAST(lastModifiedDateTime AS TIMESTAMP) DESC NULLS LAST,
                    TRY_CAST(_extracted_at AS TIMESTAMP) DESC NULLS LAST,
                    TRY_CAST(load_date AS DATE) DESC NULLS LAST,
                    CAST(batch_id AS VARCHAR) DESC NULLS LAST
            ) AS _rn
        FROM raw
        WHERE externalCode IS NOT NULL
    )
    WHERE _rn = 1
)
SELECT
    externalCode AS payment_detail_id,
    PaymentInformationV3_worker AS worker_id,
    TRY_CAST(PaymentInformationV3_effectiveStartDate AS DATE) AS effective_start_date,
    TRY_CAST(mdfSystemEffectiveStartDate AS DATE) AS system_effective_start_date,
    TRY_CAST(mdfSystemEffectiveEndDate AS DATE) AS system_effective_end_date,
    paymentMethod AS payment_method,
    bankCountry AS bank_country,
    bank AS bank,
    businessIdentifierCode AS business_identifier_code,
    routingNumber AS routing_number,
    accountNumber AS account_number,
    accountOwner AS account_owner,
    iban AS iban,
    currency AS currency,
    TRY_CAST(amount AS DOUBLE) AS amount,
    TRY_CAST(percent AS DOUBLE) AS percent,
    payType AS pay_type,
    customPayType AS custom_pay_type,
    paySequence AS pay_sequence,
    purpose AS purpose,
    load_date
FROM latest
ORDER BY worker_id, payment_detail_id
$seed$, $seed$Última extracción deduplicada de PaymentInformationDetailV3. Campos bancarios sensibles llegan masked/encrypted desde bronze.$seed$, $seed${}$seed$::jsonb, $seed$$seed$, NOW(), (SELECT id FROM workspaces ORDER BY created_at ASC LIMIT 1)),
($seed$sap_successfactors_peremail_latest$seed$, $seed$silver$seed$, $seed$sap_successfactors$seed$, $seed$["raw/sap_successfactors/PerEmail"]$seed$::jsonb, $seed$
-- sap_successfactors_peremail_latest  (silver)  cartridge: sap_successfactors
-- sources: ["raw/sap_successfactors/PerEmail"]
-- description: Última extracción deduplicada de PerEmail. emailAddress llega masked desde bronze; personIdExternal/emailType quedan planos para cruces.

WITH raw AS (
    SELECT *
    FROM read_parquet('s3://{bucket}/raw/sap_successfactors/PerEmail/**/*.parquet',
                      hive_partitioning = true,
                      union_by_name   = true)
),
latest AS (
    SELECT *
    FROM (
        SELECT
            raw.*,
            ROW_NUMBER() OVER (
                PARTITION BY personIdExternal, emailType, emailAddress
                ORDER BY
                    TRY_CAST(lastModifiedDateTime AS TIMESTAMP) DESC NULLS LAST,
                    TRY_CAST(_extracted_at AS TIMESTAMP) DESC NULLS LAST,
                    TRY_CAST(load_date AS DATE) DESC NULLS LAST,
                    CAST(batch_id AS VARCHAR) DESC NULLS LAST
            ) AS _rn
        FROM raw
        WHERE personIdExternal IS NOT NULL
    )
    WHERE _rn = 1
)
SELECT
    personIdExternal AS person_id_external,
    emailType        AS email_type,
    emailAddress     AS email_address,
    TRY_CAST(isPrimary AS BOOLEAN) AS is_primary,
    load_date
FROM latest
ORDER BY person_id_external, email_type, email_address
$seed$, $seed$Última extracción deduplicada de PerEmail. emailAddress llega masked desde bronze; personIdExternal/emailType quedan planos para cruces.$seed$, $seed${}$seed$::jsonb, $seed$$seed$, NOW(), (SELECT id FROM workspaces ORDER BY created_at ASC LIMIT 1)),
($seed$sap_successfactors_perperson_latest$seed$, $seed$silver$seed$, $seed$sap_successfactors$seed$, $seed$["raw/sap_successfactors/PerPerson"]$seed$::jsonb, $seed$
-- sap_successfactors_perperson_latest  (silver)  cartridge: sap_successfactors
-- sources: ["raw/sap_successfactors/PerPerson"]
-- description: Última extracción de PerPerson (persona EC). personIdExternal shadowed y dateOfBirth encrypted desde bronze (caja negra, no agregable).

WITH raw AS (
    SELECT *
    FROM read_parquet('s3://{bucket}/raw/sap_successfactors/PerPerson/**/*.parquet',
                      hive_partitioning = true,
                      union_by_name   = true)
),
latest AS (
    SELECT *
    FROM (
        SELECT
            raw.*,
            ROW_NUMBER() OVER (
                PARTITION BY personIdExternal
                ORDER BY
                    TRY_CAST(lastModifiedDateTime AS TIMESTAMP) DESC NULLS LAST,
                    TRY_CAST(_extracted_at AS TIMESTAMP) DESC NULLS LAST,
                    TRY_CAST(load_date AS DATE) DESC NULLS LAST,
                    CAST(batch_id AS VARCHAR) DESC NULLS LAST
            ) AS _rn
        FROM raw
        WHERE personIdExternal IS NOT NULL
    )
    WHERE _rn = 1
)
SELECT
    personIdExternal     AS person_id_external,  -- shadowed en bronze
    personId             AS person_id,
    dateOfBirth          AS date_of_birth,        -- encrypted en bronze (token Fernet)
    countryOfBirth       AS country_of_birth,
    load_date
FROM latest
ORDER BY person_id_external
$seed$, $seed$Última extracción de PerPerson (persona EC). personIdExternal shadowed y dateOfBirth encrypted desde bronze (caja negra, no agregable).$seed$, $seed${}$seed$::jsonb, $seed$$seed$, NOW(), (SELECT id FROM workspaces ORDER BY created_at ASC LIMIT 1)),
($seed$sap_successfactors_perpersonal_latest$seed$, $seed$silver$seed$, $seed$sap_successfactors$seed$, $seed$["raw/sap_successfactors/PerPersonal"]$seed$::jsonb, $seed$
-- sap_successfactors_perpersonal_latest  (silver)  cartridge: sap_successfactors
-- sources: ["raw/sap_successfactors/PerPersonal"]
-- description: Última extracción de PerPersonal (datos personales efectivo-fechados). Nombre masked desde bronze; personIdExternal en claro (casa con Emp*).

WITH raw AS (
    SELECT *
    FROM read_parquet('s3://{bucket}/raw/sap_successfactors/PerPersonal/**/*.parquet',
                      hive_partitioning = true,
                      union_by_name   = true)
),
latest AS (
    SELECT *
    FROM (
        SELECT
            raw.*,
            ROW_NUMBER() OVER (
                PARTITION BY personIdExternal, startDate
                ORDER BY
                    TRY_CAST(lastModifiedDateTime AS TIMESTAMP) DESC NULLS LAST,
                    TRY_CAST(_extracted_at AS TIMESTAMP) DESC NULLS LAST,
                    TRY_CAST(load_date AS DATE) DESC NULLS LAST,
                    CAST(batch_id AS VARCHAR) DESC NULLS LAST
            ) AS _rn
        FROM raw
        WHERE personIdExternal IS NOT NULL
    )
    WHERE _rn = 1
)
SELECT
    personIdExternal     AS person_id_external,   -- plano (sin regla de protección)
    firstName            AS first_name,           -- masked en bronze
    lastName             AS last_name,            -- masked en bronze
    gender               AS gender,
    maritalStatus        AS marital_status,
    TRY_CAST(startDate AS DATE) AS valid_from,
    load_date
FROM latest
ORDER BY person_id_external, valid_from
$seed$, $seed$Última extracción de PerPersonal (datos personales efectivo-fechados). Nombre masked desde bronze; personIdExternal en claro (casa con Emp*).$seed$, $seed${}$seed$::jsonb, $seed$$seed$, NOW(), (SELECT id FROM workspaces ORDER BY created_at ASC LIMIT 1)),
($seed$sap_successfactors_position_latest$seed$, $seed$silver$seed$, $seed$sap_successfactors$seed$, $seed$["raw/sap_successfactors/Position"]$seed$::jsonb, $seed$
-- sap_successfactors_position_latest  (silver)  cartridge: sap_successfactors
-- sources: ["raw/sap_successfactors/Position"]
-- description: Última extracción de Position Management (posiciones).

-- NOTA: Position no declara select_fields; campos SF estándar (code, nombre, org).
WITH raw AS (
    SELECT *
    FROM read_parquet('s3://{bucket}/raw/sap_successfactors/Position/**/*.parquet',
                      hive_partitioning = true,
                      union_by_name   = true)
),
latest AS (
    SELECT *
    FROM (
        SELECT
            raw.*,
            ROW_NUMBER() OVER (
                PARTITION BY code
                ORDER BY
                    TRY_CAST(lastModifiedDateTime AS TIMESTAMP) DESC NULLS LAST,
                    TRY_CAST(_extracted_at AS TIMESTAMP) DESC NULLS LAST,
                    TRY_CAST(load_date AS DATE) DESC NULLS LAST,
                    CAST(batch_id AS VARCHAR) DESC NULLS LAST
            ) AS _rn
        FROM raw
        WHERE code IS NOT NULL
    )
    WHERE _rn = 1
)
SELECT
    code                       AS position_id,
    externalName_defaultValue  AS position_name,
    department                 AS department,
    location                   AS location,
    costCenter                 AS cost_center,
    load_date
FROM latest
ORDER BY position_id
$seed$, $seed$Última extracción de Position Management (posiciones).$seed$, $seed${}$seed$::jsonb, $seed$$seed$, NOW(), (SELECT id FROM workspaces ORDER BY created_at ASC LIMIT 1)),
($seed$sap_successfactors_recruitment_funnel$seed$, $seed$gold$seed$, $seed$sap_successfactors$seed$, $seed$["raw/sap_successfactors/JobRequisition", "raw/sap_successfactors/Candidate"]$seed$::jsonb, $seed$
-- sap_successfactors_recruitment_funnel  (gold)  cartridge: sap_successfactors
-- sources: ["raw/sap_successfactors/JobRequisition", "raw/sap_successfactors/Candidate"]
-- description: Embudo de reclutamiento por departamento: requisiciones totales y abiertas. Las etapas por candidato requieren JobApplication (no extraída).

-- TODO: el detalle por etapa (aplicado -> entrevista -> oferta) requiere
-- JobApplication (no extraída). Aquí se reportan contadores de requisición.
WITH pipe AS (
    SELECT job_req_id, department, status, candidate_pool_total
    FROM read_parquet('s3://{bucket}/silver/sap_successfactors/sap_successfactors_recruitment_pipeline/**/*.parquet')
)
SELECT
    COALESCE(department, '(sin departamento)')                                   AS department,
    COUNT(DISTINCT job_req_id)                                                   AS requisitions,
    COUNT(DISTINCT CASE WHEN status IS DISTINCT FROM 'Closed' AND status IS DISTINCT FROM 'Filled' THEN job_req_id END) AS open_requisitions,
    MAX(candidate_pool_total)                                                    AS candidate_pool
FROM pipe
GROUP BY department
ORDER BY open_requisitions DESC, department
$seed$, $seed$Embudo de reclutamiento por departamento: requisiciones totales y abiertas. Las etapas por candidato requieren JobApplication (no extraída).$seed$, $seed${}$seed$::jsonb, $seed$$seed$, NOW(), (SELECT id FROM workspaces ORDER BY created_at ASC LIMIT 1)),
($seed$sap_successfactors_recruitment_pipeline$seed$, $seed$silver$seed$, $seed$sap_successfactors$seed$, $seed$["raw/sap_successfactors/JobRequisition", "silver/sap_successfactors/sap_successfactors_jobrequisition_latest"]$seed$::jsonb, $seed$
-- sap_successfactors_recruitment_pipeline  (silver)  cartridge: sap_successfactors
-- sources: ["raw/sap_successfactors/JobRequisition", "silver/sap_successfactors/sap_successfactors_jobrequisition_latest"]
-- description: Pipeline de reclutamiento centrado en la requisición. Candidate está bloqueado por permisos en este tenant; el conteo queda en 0 hasta habilitar JobApplication/Candidate.

-- TODO: el enlace requisición -> candidato está en JobApplication (no extraída).
-- Candidate no se consulta aquí porque la entidad requiere permisos OData
-- adicionales y bloquea la materialización del dataset.
WITH reqs AS (
    SELECT job_req_id, job_title, status, department, location
    FROM read_parquet('s3://{bucket}/silver/sap_successfactors/sap_successfactors_jobrequisition_latest/**/*.parquet')
)
SELECT
    r.job_req_id         AS job_req_id,
    r.job_title          AS job_title,
    r.status             AS status,
    r.department         AS department,
    r.location           AS location,
    CAST(0 AS BIGINT)    AS candidate_pool_total   -- TODO: por requisición vía JobApplication
FROM reqs r
ORDER BY r.job_req_id
$seed$, $seed$Pipeline de reclutamiento centrado en la requisición. Candidate está bloqueado por permisos en este tenant; el conteo queda en 0 hasta habilitar JobApplication/Candidate.$seed$, $seed${}$seed$::jsonb, $seed$$seed$, NOW(), (SELECT id FROM workspaces ORDER BY created_at ASC LIMIT 1)),
($seed$sap_successfactors_turnover_by_period$seed$, $seed$gold$seed$, $seed$sap_successfactors$seed$, $seed$["raw/sap_successfactors/EmpEmploymentTermination"]$seed$::jsonb, $seed$
-- sap_successfactors_turnover_by_period  (gold)  cartridge: sap_successfactors
-- sources: ["raw/sap_successfactors/EmpEmploymentTermination"]
-- description: Rotación de personal: bajas por mes y motivo desde EmpEmploymentTermination.

-- NOTA: EmpEmploymentTermination está registrado en entity_config por el seed de
-- completitud de SAP SuccessFactors; este gold queda vacío solo si el tenant no
-- trae bajas en la ventana extraída.
WITH term AS (
    SELECT user_id, termination_date, event_reason
    FROM read_parquet('s3://{bucket}/silver/sap_successfactors/sap_successfactors_empemploymenttermination_latest/**/*.parquet')
    WHERE termination_date IS NOT NULL
)
SELECT
    CAST(DATE_TRUNC('month', termination_date) AS DATE) AS termination_month,
    COALESCE(event_reason, '(sin motivo)')              AS event_reason,
    COUNT(DISTINCT user_id)                             AS terminations
FROM term
GROUP BY DATE_TRUNC('month', termination_date), event_reason
ORDER BY termination_month DESC, terminations DESC
$seed$, $seed$Rotación de personal: bajas por mes y motivo desde EmpEmploymentTermination.$seed$, $seed${}$seed$::jsonb, $seed$$seed$, NOW(), (SELECT id FROM workspaces ORDER BY created_at ASC LIMIT 1)),
($seed$sap_successfactors_user_latest$seed$, $seed$silver$seed$, $seed$sap_successfactors$seed$, $seed$["raw/sap_successfactors/User"]$seed$::jsonb, $seed$
-- sap_successfactors_user_latest  (silver)  cartridge: sap_successfactors
-- sources: ["raw/sap_successfactors/User"]
-- description: Última extracción del maestro de usuarios (User). userId llega shadowed y nombre/email masked desde bronze.

WITH raw AS (
    SELECT *
    FROM read_parquet('s3://{bucket}/raw/sap_successfactors/User/**/*.parquet',
                      hive_partitioning = true,
                      union_by_name   = true)
),
latest AS (
    SELECT *
    FROM (
        SELECT
            raw.*,
            ROW_NUMBER() OVER (
                PARTITION BY userId
                ORDER BY
                    TRY_CAST(lastModifiedDateTime AS TIMESTAMP) DESC NULLS LAST,
                    TRY_CAST(_extracted_at AS TIMESTAMP) DESC NULLS LAST,
                    TRY_CAST(load_date AS DATE) DESC NULLS LAST,
                    CAST(batch_id AS VARCHAR) DESC NULLS LAST
            ) AS _rn
        FROM raw
        WHERE userId IS NOT NULL
    )
    WHERE _rn = 1
)
SELECT
    userId               AS user_id,            -- shadowed en bronze (hash; NO casa con userId plano de Emp*)
    username             AS username,
    email                AS email,              -- masked en bronze
    status               AS status,
    firstName            AS first_name,         -- masked en bronze
    lastName             AS last_name,          -- masked en bronze
    department           AS department,
    division             AS division,
    location             AS location,
    manager              AS manager,
    CAST(hireDate AS DATE) AS hire_date,
    load_date
FROM latest
ORDER BY user_id
$seed$, $seed$Última extracción del maestro de usuarios (User). userId llega shadowed y nombre/email masked desde bronze.$seed$, $seed${}$seed$::jsonb, $seed$$seed$, NOW(), (SELECT id FROM workspaces ORDER BY created_at ASC LIMIT 1))
ON CONFLICT (name) DO NOTHING;

INSERT INTO schema_migrations (filename, applied_at)
VALUES ('82_sap_successfactors_datasets_seed.sql', NOW())
ON CONFLICT (filename) DO NOTHING;
