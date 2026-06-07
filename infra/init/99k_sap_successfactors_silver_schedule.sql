-- 99k_sap_successfactors_silver_schedule.sql
--
-- Keeps the live SuccessFactors Silver catalog aligned with the entities enabled
-- after PerPerson, fixes _latest deduplication to choose the newest snapshot per
-- business key, and schedules the FEMSA scoped connection for daily extraction.
--
-- Existing installs already applied migration 82, so this migration uses
-- ON CONFLICT DO UPDATE for the affected Silver datasets.

ALTER TABLE entity_config
    ADD COLUMN IF NOT EXISTS tenant_id UUID REFERENCES tenants(id) ON DELETE SET NULL;

ALTER TABLE entity_config
    ADD COLUMN IF NOT EXISTS workspace_id UUID REFERENCES workspaces(id) ON DELETE SET NULL;

INSERT INTO datasets (name, layer, cartridge, sources, sql_def, description, column_mapping, schedule, updated_at, workspace_id)
VALUES
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
latest AS (
    SELECT *
    FROM (
        SELECT
            raw.*,
            ROW_NUMBER() OVER (
                PARTITION BY personIdExternal, userId, startDate
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
    personIdExternal     AS person_id_external,   -- plano
    userId               AS user_id,              -- plano
    TRY_CAST(startDate AS DATE)     AS start_date,
    TRY_CAST(endDate AS DATE)       AS end_date,
    assignmentClass      AS employee_class,
    TRY_CAST(originalStartDate AS DATE) AS original_start_date,
    load_date
FROM latest
ORDER BY user_id, start_date
$seed$, $seed$Última extracción de EmpEmployment (relación laboral). Puente entre userId y personIdExternal (ambos planos).$seed$, $seed${}$seed$::jsonb, $seed$$seed$, NOW(), (SELECT id FROM workspaces ORDER BY created_at ASC LIMIT 1)),
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
$seed$, $seed$Estructura organizacional observada: combinaciones distintas de compañía/división/departamento/ubicación según las asignaciones de EmpJob, con nombres de los FO.$seed$, $seed${}$seed$::jsonb, $seed$$seed$, NOW(), (SELECT id FROM workspaces ORDER BY created_at ASC LIMIT 1))
ON CONFLICT (name) DO UPDATE SET
    layer          = EXCLUDED.layer,
    cartridge      = EXCLUDED.cartridge,
    sources        = EXCLUDED.sources,
    sql_def        = EXCLUDED.sql_def,
    description    = EXCLUDED.description,
    column_mapping = EXCLUDED.column_mapping,
    schedule       = EXCLUDED.schedule,
    workspace_id   = COALESCE(datasets.workspace_id, EXCLUDED.workspace_id),
    updated_at     = NOW();

UPDATE entity_config
   SET select_fields = '["personIdExternal","userId","startDate","endDate","assignmentClass","originalStartDate","lastModifiedDateTime"]'::jsonb
 WHERE cartridge_id = 'sap_successfactors'
   AND entity = 'EmpEmployment';

-- FEMSA AWS scoped schedule. Guarded so generic/local installs without this
-- tenant/workspace keep manual mode instead of pointing to a nonexistent scope.
WITH femsa_scope AS (
    SELECT
        'b95f4d58-c9c8-4fd5-8d07-ddde294c7d78'::uuid AS tenant_id,
        'a2b1ced2-4d92-4bbe-8f9f-9a7cc88bb9f4'::uuid AS workspace_id
    WHERE EXISTS (SELECT 1 FROM tenants WHERE id = 'b95f4d58-c9c8-4fd5-8d07-ddde294c7d78'::uuid)
      AND EXISTS (SELECT 1 FROM workspaces WHERE id = 'a2b1ced2-4d92-4bbe-8f9f-9a7cc88bb9f4'::uuid)
), desired(entity, cron_expression) AS (
    VALUES
        ('PerPerson', '0 2 * * *'),
        ('PerPersonal', '10 2 * * *'),
        ('PerEmail', '20 2 * * *'),
        ('EmpEmployment', '30 2 * * *'),
        ('EmpJob', '40 2 * * *'),
        ('PaymentInformationDetailV3', '50 2 * * *'),
        ('FOLocation', '0 3 * * *')
)
UPDATE entity_config ec
   SET trigger_type      = 'scheduled',
       cron_expression   = desired.cron_expression,
       connection_id     = 'femsa_sf',
       dag_id            = COALESCE(NULLIF(ec.dag_id, ''), 'sap_successfactors_extract'),
       tenant_id         = femsa_scope.tenant_id,
       workspace_id      = femsa_scope.workspace_id,
       dag_params        = COALESCE(ec.dag_params, '{}'::jsonb)
  FROM desired, femsa_scope
 WHERE ec.cartridge_id = 'sap_successfactors'
   AND ec.entity = desired.entity;

INSERT INTO schema_migrations (filename, applied_at)
VALUES ('99k_sap_successfactors_silver_schedule.sql', NOW())
ON CONFLICT (filename) DO NOTHING;
