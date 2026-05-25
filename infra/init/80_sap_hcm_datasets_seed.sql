-- 80_sap_hcm_datasets_seed.sql
--
-- Seeds the SAP HCM silver/gold datasets into the `datasets` catalog table.
-- Mirrors infra/init/65_replicon_mejoras_seed_refresh.sql (a post-workspace_id
-- dataset seed): the per-dataset SQL bodies live in cartridges/sap_hcm/datasets/
-- *.sql (the source of truth for review) and are inlined here as dollar-quoted
-- literals so refinement can materialize them.
--
-- workspace_id (NOT NULL since migration 23) is set to the first workspace, the
-- same backfill target migration 23 uses; this migration runs after 23 so the
-- column must be provided explicitly (unlike migration 10, which predates it).
--
-- 13 silver + 8 gold = 21 datasets. Idempotent: ON CONFLICT (name) DO NOTHING.
-- Registered via this infra migration (not the cartridge seed.sql) because the
-- cartridge seed guard disallows INSERT INTO datasets / INSERT ... SELECT.

INSERT INTO datasets (name, layer, cartridge, sources, sql_def, description, column_mapping, schedule, updated_at, workspace_id)
VALUES
($seed$sap_hcm_employeemaster_latest$seed$, $seed$silver$seed$, $seed$sap_hcm$seed$, $seed$["raw/sap_hcm/EmployeeMaster"]$seed$::jsonb, $seed$
WITH latest AS (
    -- Solo la última extracción (snapshot más reciente en bronze).
    SELECT *
    FROM read_parquet('s3://{bucket}/raw/sap_hcm/EmployeeMaster/**/*.parquet',
                      hive_partitioning = true,
                      union_by_name   = true)
    WHERE load_date = (SELECT MAX(load_date)
                       FROM read_parquet('s3://{bucket}/raw/sap_hcm/EmployeeMaster/**/*.parquet',
                                          hive_partitioning = true))
)
SELECT
    Pernr                 AS pernr,            -- shadowed en bronze: hash estable, usado como FK
    CAST(Begda AS DATE)   AS valid_from,
    CAST(Endda AS DATE)   AS valid_to,
    Bukrs                 AS company_code,
    Werks                 AS personnel_area,
    Persg                 AS employee_group,
    Persk                 AS employee_subgroup,
    Orgeh                 AS org_unit_id,
    Plans                 AS position_id,
    Kostl                 AS cost_center,
    AedtmAed              AS changed_on,
    load_date
FROM latest
ORDER BY pernr, valid_from
$seed$, $seed$Última extracción de EmployeeMaster (PA0001) con campos tipados, filtrada por el load_date más reciente.$seed$, $seed${}$seed$::jsonb, $seed$$seed$, NOW(), (SELECT id FROM workspaces ORDER BY created_at ASC LIMIT 1)),
($seed$sap_hcm_personaldata_latest$seed$, $seed$silver$seed$, $seed$sap_hcm$seed$, $seed$["raw/sap_hcm/PersonalData"]$seed$::jsonb, $seed$
WITH latest AS (
    SELECT *
    FROM read_parquet('s3://{bucket}/raw/sap_hcm/PersonalData/**/*.parquet',
                      hive_partitioning = true,
                      union_by_name   = true)
    WHERE load_date = (SELECT MAX(load_date)
                       FROM read_parquet('s3://{bucket}/raw/sap_hcm/PersonalData/**/*.parquet',
                                          hive_partitioning = true))
)
SELECT
    Pernr                AS pernr,           -- shadowed en bronze
    Vorna                AS first_name,      -- masked en bronze
    Nachn                AS last_name,       -- masked en bronze
    Gbdat                AS birth_date,      -- encrypted en bronze (token Fernet)
    Gesch                AS gender,
    Famst                AS marital_status,
    AedtmAed             AS changed_on,
    load_date
FROM latest
ORDER BY pernr
$seed$, $seed$Última extracción de PersonalData (PA0002) con campos tipados. Nombre y fecha de nacimiento llegan ya protegidos desde bronze (masked / encrypted).$seed$, $seed${}$seed$::jsonb, $seed$$seed$, NOW(), (SELECT id FROM workspaces ORDER BY created_at ASC LIMIT 1)),
($seed$sap_hcm_employeeactions_latest$seed$, $seed$silver$seed$, $seed$sap_hcm$seed$, $seed$["raw/sap_hcm/EmployeeActions"]$seed$::jsonb, $seed$
WITH latest AS (
    SELECT *
    FROM read_parquet('s3://{bucket}/raw/sap_hcm/EmployeeActions/**/*.parquet',
                      hive_partitioning = true,
                      union_by_name   = true)
    WHERE load_date = (SELECT MAX(load_date)
                       FROM read_parquet('s3://{bucket}/raw/sap_hcm/EmployeeActions/**/*.parquet',
                                          hive_partitioning = true))
)
SELECT
    Pernr                AS pernr,           -- shadowed en bronze
    CAST(Begda AS DATE)  AS valid_from,
    CAST(Endda AS DATE)  AS valid_to,
    Massn                AS action_type,     -- p.ej. 01=alta, 02=baja, 04=traslado
    Massg                AS action_reason,
    AedtmAed             AS changed_on,
    load_date
FROM latest
ORDER BY pernr, valid_from
$seed$, $seed$Última extracción de EmployeeActions (PA0000): altas, bajas y traslados, con campos tipados.$seed$, $seed${}$seed$::jsonb, $seed$$seed$, NOW(), (SELECT id FROM workspaces ORDER BY created_at ASC LIMIT 1)),
($seed$sap_hcm_contractdata_latest$seed$, $seed$silver$seed$, $seed$sap_hcm$seed$, $seed$["raw/sap_hcm/ContractData"]$seed$::jsonb, $seed$
-- NOTA: ContractData no declara select_fields en entities.yaml; los nombres de
-- campo siguen el estándar SAP PA0016 (Cttyp = tipo de contrato). Ajustar a la
-- nomenclatura real del tenant si difiere.
WITH latest AS (
    SELECT *
    FROM read_parquet('s3://{bucket}/raw/sap_hcm/ContractData/**/*.parquet',
                      hive_partitioning = true,
                      union_by_name   = true)
    WHERE load_date = (SELECT MAX(load_date)
                       FROM read_parquet('s3://{bucket}/raw/sap_hcm/ContractData/**/*.parquet',
                                          hive_partitioning = true))
)
SELECT
    Pernr                AS pernr,           -- shadowed en bronze
    CAST(Begda AS DATE)  AS valid_from,
    CAST(Endda AS DATE)  AS valid_to,
    Cttyp                AS contract_type,
    load_date
FROM latest
ORDER BY pernr, valid_from
$seed$, $seed$Última extracción de ContractData (PA0016): elementos de contrato por empleado.$seed$, $seed${}$seed$::jsonb, $seed$$seed$, NOW(), (SELECT id FROM workspaces ORDER BY created_at ASC LIMIT 1)),
($seed$sap_hcm_orgunit_latest$seed$, $seed$silver$seed$, $seed$sap_hcm$seed$, $seed$["raw/sap_hcm/OrgUnit"]$seed$::jsonb, $seed$
-- HRP1000 estándar: Objid = id del objeto OM, Stext = texto largo, Short = texto corto.
WITH latest AS (
    SELECT *
    FROM read_parquet('s3://{bucket}/raw/sap_hcm/OrgUnit/**/*.parquet',
                      hive_partitioning = true,
                      union_by_name   = true)
    WHERE load_date = (SELECT MAX(load_date)
                       FROM read_parquet('s3://{bucket}/raw/sap_hcm/OrgUnit/**/*.parquet',
                                          hive_partitioning = true))
)
SELECT
    Objid                AS org_unit_id,
    Stext                AS org_unit_name,
    Short                AS org_unit_short,
    CAST(Begda AS DATE)  AS valid_from,
    CAST(Endda AS DATE)  AS valid_to,
    load_date
FROM latest
ORDER BY org_unit_id, valid_from
$seed$, $seed$Última extracción de OrgUnit (HRP1000 Otype=O): unidades organizacionales con id y nombre.$seed$, $seed${}$seed$::jsonb, $seed$$seed$, NOW(), (SELECT id FROM workspaces ORDER BY created_at ASC LIMIT 1)),
($seed$sap_hcm_position_latest$seed$, $seed$silver$seed$, $seed$sap_hcm$seed$, $seed$["raw/sap_hcm/Position"]$seed$::jsonb, $seed$
WITH latest AS (
    SELECT *
    FROM read_parquet('s3://{bucket}/raw/sap_hcm/Position/**/*.parquet',
                      hive_partitioning = true,
                      union_by_name   = true)
    WHERE load_date = (SELECT MAX(load_date)
                       FROM read_parquet('s3://{bucket}/raw/sap_hcm/Position/**/*.parquet',
                                          hive_partitioning = true))
)
SELECT
    Objid                AS position_id,
    Stext                AS position_name,
    Short                AS position_short,
    CAST(Begda AS DATE)  AS valid_from,
    CAST(Endda AS DATE)  AS valid_to,
    load_date
FROM latest
ORDER BY position_id, valid_from
$seed$, $seed$Última extracción de Position (HRP1000 Otype=S): posiciones con id y nombre.$seed$, $seed${}$seed$::jsonb, $seed$$seed$, NOW(), (SELECT id FROM workspaces ORDER BY created_at ASC LIMIT 1)),
($seed$sap_hcm_jobcode_latest$seed$, $seed$silver$seed$, $seed$sap_hcm$seed$, $seed$["raw/sap_hcm/JobCode"]$seed$::jsonb, $seed$
WITH latest AS (
    SELECT *
    FROM read_parquet('s3://{bucket}/raw/sap_hcm/JobCode/**/*.parquet',
                      hive_partitioning = true,
                      union_by_name   = true)
    WHERE load_date = (SELECT MAX(load_date)
                       FROM read_parquet('s3://{bucket}/raw/sap_hcm/JobCode/**/*.parquet',
                                          hive_partitioning = true))
)
SELECT
    Objid                AS job_code_id,
    Stext                AS job_code_name,
    Short                AS job_code_short,
    CAST(Begda AS DATE)  AS valid_from,
    CAST(Endda AS DATE)  AS valid_to,
    load_date
FROM latest
ORDER BY job_code_id, valid_from
$seed$, $seed$Última extracción de JobCode (HRP1000 Otype=C): trabajos / clasificaciones con id y nombre.$seed$, $seed${}$seed$::jsonb, $seed$$seed$, NOW(), (SELECT id FROM workspaces ORDER BY created_at ASC LIMIT 1)),
($seed$sap_hcm_costcenter_latest$seed$, $seed$silver$seed$, $seed$sap_hcm$seed$, $seed$["raw/sap_hcm/CostCenter"]$seed$::jsonb, $seed$
WITH latest AS (
    SELECT *
    FROM read_parquet('s3://{bucket}/raw/sap_hcm/CostCenter/**/*.parquet',
                      hive_partitioning = true,
                      union_by_name   = true)
    WHERE load_date = (SELECT MAX(load_date)
                       FROM read_parquet('s3://{bucket}/raw/sap_hcm/CostCenter/**/*.parquet',
                                          hive_partitioning = true))
)
SELECT
    Pernr                AS pernr,           -- shadowed en bronze
    Kostl                AS cost_center,
    Orgeh                AS org_unit_id,
    CAST(Begda AS DATE)  AS valid_from,
    CAST(Endda AS DATE)  AS valid_to,
    AedtmAed             AS changed_on,
    load_date
FROM latest
ORDER BY cost_center, pernr
$seed$, $seed$Última extracción de CostCenter (subconjunto de PA0001 con Kostl) con campos tipados.$seed$, $seed${}$seed$::jsonb, $seed$$seed$, NOW(), (SELECT id FROM workspaces ORDER BY created_at ASC LIMIT 1)),
($seed$sap_hcm_leaveabsence_latest$seed$, $seed$silver$seed$, $seed$sap_hcm$seed$, $seed$["raw/sap_hcm/LeaveAbsence"]$seed$::jsonb, $seed$
-- Nombres de campo alineados con los KBs existentes (kb_absence_analysis):
-- Awart = tipo de ausencia, Abwtg = días hábiles, Kaltd = días calendario.
WITH latest AS (
    SELECT *
    FROM read_parquet('s3://{bucket}/raw/sap_hcm/LeaveAbsence/**/*.parquet',
                      hive_partitioning = true,
                      union_by_name   = true)
    WHERE load_date = (SELECT MAX(load_date)
                       FROM read_parquet('s3://{bucket}/raw/sap_hcm/LeaveAbsence/**/*.parquet',
                                          hive_partitioning = true))
)
SELECT
    Pernr                        AS pernr,          -- shadowed en bronze
    CAST(Begda AS DATE)          AS valid_from,
    CAST(Endda AS DATE)          AS valid_to,
    Awart                        AS absence_type,
    CAST(Abwtg AS DECIMAL(7,2))  AS absence_days_workable,
    CAST(Kaltd AS DECIMAL(7,2))  AS absence_days_calendar,
    load_date
FROM latest
ORDER BY pernr, valid_from
$seed$, $seed$Última extracción de LeaveAbsence (PA2001): ausencias por empleado, tipo y días.$seed$, $seed${}$seed$::jsonb, $seed$$seed$, NOW(), (SELECT id FROM workspaces ORDER BY created_at ASC LIMIT 1)),
($seed$sap_hcm_workschedule_latest$seed$, $seed$silver$seed$, $seed$sap_hcm$seed$, $seed$["raw/sap_hcm/WorkSchedule"]$seed$::jsonb, $seed$
-- NOTA: PA0007 no declara select_fields; nombres SAP estándar (Schkz = regla de
-- horario, Empct = porcentaje de jornada). Ajustar si el tenant difiere.
WITH latest AS (
    SELECT *
    FROM read_parquet('s3://{bucket}/raw/sap_hcm/WorkSchedule/**/*.parquet',
                      hive_partitioning = true,
                      union_by_name   = true)
    WHERE load_date = (SELECT MAX(load_date)
                       FROM read_parquet('s3://{bucket}/raw/sap_hcm/WorkSchedule/**/*.parquet',
                                          hive_partitioning = true))
)
SELECT
    Pernr                  AS pernr,         -- shadowed en bronze
    CAST(Begda AS DATE)    AS valid_from,
    CAST(Endda AS DATE)    AS valid_to,
    Schkz                  AS work_schedule_rule,
    CAST(Empct AS DECIMAL(5,2)) AS employment_percent,
    load_date
FROM latest
ORDER BY pernr, valid_from
$seed$, $seed$Última extracción de WorkSchedule (PA0007): horario de trabajo planificado por empleado.$seed$, $seed${}$seed$::jsonb, $seed$$seed$, NOW(), (SELECT id FROM workspaces ORDER BY created_at ASC LIMIT 1)),
($seed$sap_hcm_employee_master_full$seed$, $seed$silver$seed$, $seed$sap_hcm$seed$, $seed$["raw/sap_hcm/EmployeeMaster", "raw/sap_hcm/PersonalData", "raw/sap_hcm/ContractData", "raw/sap_hcm/OrgUnit", "raw/sap_hcm/Position", "raw/sap_hcm/CostCenter"]$seed$::jsonb, $seed$
WITH
-- 1. Asignación organizacional vigente (PA0001): el registro cuyo rango cubre hoy.
emp AS (
    SELECT pernr, org_unit_id, position_id, cost_center, company_code,
           personnel_area, employee_group, employee_subgroup, valid_from, valid_to,
           ROW_NUMBER() OVER (PARTITION BY pernr ORDER BY valid_from DESC) AS rn
    FROM read_parquet('s3://{bucket}/silver/sap_hcm/sap_hcm_employeemaster_latest/**/*.parquet')
    WHERE CAST(valid_from AS DATE) <= CURRENT_DATE
      AND CAST(valid_to   AS DATE) >= CURRENT_DATE
),
-- 2. Datos personales (nombre ya viene masked desde bronze; no se expone fecha de nacimiento cifrada).
pers AS (
    SELECT pernr, first_name, last_name, gender, marital_status
    FROM read_parquet('s3://{bucket}/silver/sap_hcm/sap_hcm_personaldata_latest/**/*.parquet')
),
-- 3. Fin de contrato vigente (PA0016) para derivar actividad.
contract AS (
    SELECT pernr, MAX(CAST(valid_to AS DATE)) AS contract_end
    FROM read_parquet('s3://{bucket}/silver/sap_hcm/sap_hcm_contractdata_latest/**/*.parquet')
    GROUP BY pernr
),
-- 4. Maestros de organización y posición para resolver nombres.
org AS (
    SELECT org_unit_id, org_unit_name
    FROM read_parquet('s3://{bucket}/silver/sap_hcm/sap_hcm_orgunit_latest/**/*.parquet')
),
pos AS (
    SELECT position_id, position_name
    FROM read_parquet('s3://{bucket}/silver/sap_hcm/sap_hcm_position_latest/**/*.parquet')
)
SELECT
    e.pernr                                              AS pernr,            -- shadowed (FK estable)
    TRIM(COALESCE(p.first_name, '') || ' ' || COALESCE(p.last_name, '')) AS full_name,  -- componentes masked en bronze
    p.gender                                             AS gender,
    p.marital_status                                     AS marital_status,
    e.company_code                                       AS company_code,
    e.personnel_area                                     AS personnel_area,
    e.employee_group                                     AS employee_group,
    e.employee_subgroup                                  AS employee_subgroup,
    e.org_unit_id                                        AS org_unit_id,
    o.org_unit_name                                      AS org_unit_name,
    e.position_id                                        AS position_id,
    ps.position_name                                     AS position_name,
    e.cost_center                                        AS cost_center,
    e.valid_from                                         AS valid_from,
    e.valid_to                                           AS valid_to,
    CASE WHEN COALESCE(c.contract_end, CAST(e.valid_to AS DATE)) >= CURRENT_DATE
         THEN TRUE ELSE FALSE END                        AS is_active
FROM emp e
LEFT JOIN pers p     ON p.pernr = e.pernr
LEFT JOIN contract c ON c.pernr = e.pernr
LEFT JOIN org o      ON o.org_unit_id = e.org_unit_id
LEFT JOIN pos ps     ON ps.position_id = e.position_id
WHERE e.rn = 1
ORDER BY e.pernr
$seed$, $seed$Vista 360 del empleado: asignación org (PA0001) + datos personales (PA0002) + contrato (PA0016), enriquecida con nombre de unidad org y posición. Una fila por empleado (registro vigente).$seed$, $seed${}$seed$::jsonb, $seed$$seed$, NOW(), (SELECT id FROM workspaces ORDER BY created_at ASC LIMIT 1)),
($seed$sap_hcm_org_hierarchy$seed$, $seed$silver$seed$, $seed$sap_hcm$seed$, $seed$["raw/sap_hcm/OrgUnit"]$seed$::jsonb, $seed$
-- TODO: la relación padre-hijo de OM vive en HRP1001Set (no extraído en Bloque A).
-- Cuando se habilite raw/sap_hcm/OrgRelationship (Subty A002), reemplazar este
-- bloque por un CTE recursivo: edges(child, parent) -> WITH RECURSIVE walk(...)
-- para poblar parent_id / depth / path reales.
WITH org AS (
    SELECT
        org_unit_id,
        org_unit_name,
        ROW_NUMBER() OVER (PARTITION BY org_unit_id ORDER BY valid_from DESC) AS rn
    FROM read_parquet('s3://{bucket}/silver/sap_hcm/sap_hcm_orgunit_latest/**/*.parquet')
)
SELECT
    org_unit_id                       AS org_id,
    org_unit_name                     AS org_name,
    CAST(NULL AS VARCHAR)             AS parent_id,
    CAST(NULL AS VARCHAR)             AS parent_name,
    0                                 AS depth,
    org_unit_name                     AS path
FROM org
WHERE rn = 1
ORDER BY org_id
$seed$, $seed$Estructura organizacional. Lista las unidades org con columnas de jerarquía. El padre queda NULL hasta habilitar HRP1001 (relaciones OM), que no se extrae hoy.$seed$, $seed${}$seed$::jsonb, $seed$$seed$, NOW(), (SELECT id FROM workspaces ORDER BY created_at ASC LIMIT 1)),
($seed$sap_hcm_position_assignment_latest$seed$, $seed$silver$seed$, $seed$sap_hcm$seed$, $seed$["raw/sap_hcm/Position", "raw/sap_hcm/EmployeeMaster", "raw/sap_hcm/PersonalData"]$seed$::jsonb, $seed$
WITH
-- 1. Posiciones (registro vigente por posición).
pos AS (
    SELECT position_id, position_name,
           ROW_NUMBER() OVER (PARTITION BY position_id ORDER BY valid_from DESC) AS rn
    FROM read_parquet('s3://{bucket}/silver/sap_hcm/sap_hcm_position_latest/**/*.parquet')
),
-- 2. Asignación vigente de empleado a posición (PA0001.Plans = Position.Objid).
emp AS (
    SELECT pernr, position_id, org_unit_id, cost_center,
           ROW_NUMBER() OVER (PARTITION BY position_id ORDER BY valid_from DESC) AS rn
    FROM read_parquet('s3://{bucket}/silver/sap_hcm/sap_hcm_employeemaster_latest/**/*.parquet')
    WHERE CAST(valid_from AS DATE) <= CURRENT_DATE
      AND CAST(valid_to   AS DATE) >= CURRENT_DATE
),
-- 3. Nombre del titular (masked en bronze).
pers AS (
    SELECT pernr,
           TRIM(COALESCE(first_name, '') || ' ' || COALESCE(last_name, '')) AS full_name
    FROM read_parquet('s3://{bucket}/silver/sap_hcm/sap_hcm_personaldata_latest/**/*.parquet')
)
SELECT
    p.position_id                                  AS position_id,
    p.position_name                                AS position_name,
    e.pernr                                        AS holder_pernr,     -- shadowed (FK)
    pr.full_name                                   AS holder_name,      -- masked
    CASE WHEN e.pernr IS NULL THEN TRUE ELSE FALSE END AS vacant,
    e.org_unit_id                                  AS org_unit_id,
    e.cost_center                                  AS cost_center
FROM pos p
LEFT JOIN emp e  ON e.position_id = p.position_id AND e.rn = 1
LEFT JOIN pers pr ON pr.pernr = e.pernr
WHERE p.rn = 1
ORDER BY p.position_id
$seed$, $seed$Posiciones con su titular actual (si existe). Marca posiciones vacantes (sin empleado asignado vía Plans).$seed$, $seed${}$seed$::jsonb, $seed$$seed$, NOW(), (SELECT id FROM workspaces ORDER BY created_at ASC LIMIT 1)),
($seed$headcount_by_department$seed$, $seed$gold$seed$, $seed$sap_hcm$seed$, $seed$["raw/sap_hcm/EmployeeMaster", "raw/sap_hcm/PersonalData", "raw/sap_hcm/ContractData", "raw/sap_hcm/OrgUnit"]$seed$::jsonb, $seed$
WITH emp AS (
    -- Empleados vigentes desde la vista 360.
    SELECT org_unit_id, org_unit_name
    FROM read_parquet('s3://{bucket}/silver/sap_hcm/sap_hcm_employee_master_full/**/*.parquet')
    WHERE is_active = TRUE
)
SELECT
    org_unit_id                              AS org_id,
    COALESCE(org_unit_name, '(sin nombre)')  AS org_name,
    COUNT(*)                                 AS headcount,
    CAST(DATE_TRUNC('month', CURRENT_DATE) AS DATE) AS snapshot_month
FROM emp
GROUP BY org_unit_id, org_unit_name
ORDER BY headcount DESC, org_id
$seed$, $seed$Empleados activos por unidad organizacional (snapshot del mes en curso).$seed$, $seed${}$seed$::jsonb, $seed$$seed$, NOW(), (SELECT id FROM workspaces ORDER BY created_at ASC LIMIT 1)),
($seed$headcount_by_costcenter$seed$, $seed$gold$seed$, $seed$sap_hcm$seed$, $seed$["raw/sap_hcm/EmployeeMaster", "raw/sap_hcm/PersonalData", "raw/sap_hcm/ContractData"]$seed$::jsonb, $seed$
WITH emp AS (
    SELECT cost_center
    FROM read_parquet('s3://{bucket}/silver/sap_hcm/sap_hcm_employee_master_full/**/*.parquet')
    WHERE is_active = TRUE
)
SELECT
    COALESCE(cost_center, '(sin centro)')           AS cost_center,
    COUNT(*)                                        AS headcount,
    CAST(DATE_TRUNC('month', CURRENT_DATE) AS DATE) AS snapshot_month
FROM emp
GROUP BY cost_center
ORDER BY headcount DESC, cost_center
$seed$, $seed$Empleados activos por centro de costo (snapshot del mes en curso).$seed$, $seed${}$seed$::jsonb, $seed$$seed$, NOW(), (SELECT id FROM workspaces ORDER BY created_at ASC LIMIT 1)),
($seed$headcount_by_position_type$seed$, $seed$gold$seed$, $seed$sap_hcm$seed$, $seed$["raw/sap_hcm/EmployeeMaster", "raw/sap_hcm/PersonalData", "raw/sap_hcm/ContractData"]$seed$::jsonb, $seed$
-- NOTA: PA0001 extraído no trae el código de puesto (Stell); como proxy de tipo
-- se usa la clasificación de personal employee_group (Persg) + subgroup (Persk).
WITH emp AS (
    SELECT employee_group, employee_subgroup
    FROM read_parquet('s3://{bucket}/silver/sap_hcm/sap_hcm_employee_master_full/**/*.parquet')
    WHERE is_active = TRUE
)
SELECT
    COALESCE(employee_group, '(sin grupo)')         AS employee_group,
    COALESCE(employee_subgroup, '(sin subgrupo)')   AS employee_subgroup,
    COUNT(*)                                        AS headcount,
    CAST(DATE_TRUNC('month', CURRENT_DATE) AS DATE) AS snapshot_month
FROM emp
GROUP BY employee_group, employee_subgroup
ORDER BY headcount DESC, employee_group, employee_subgroup
$seed$, $seed$Distribución de empleados activos por tipo de personal (employee_group / subgroup de PA0001). Proxy de "tipo de posición" hasta que se extraiga la clasificación de puesto.$seed$, $seed${}$seed$::jsonb, $seed$$seed$, NOW(), (SELECT id FROM workspaces ORDER BY created_at ASC LIMIT 1)),
($seed$absence_balance_by_employee$seed$, $seed$gold$seed$, $seed$sap_hcm$seed$, $seed$["raw/sap_hcm/LeaveAbsence"]$seed$::jsonb, $seed$
WITH absences AS (
    -- Ventana móvil de 12 meses.
    SELECT pernr, absence_type, absence_days_workable, absence_days_calendar
    FROM read_parquet('s3://{bucket}/silver/sap_hcm/sap_hcm_leaveabsence_latest/**/*.parquet')
    WHERE CAST(valid_from AS DATE) >= (CURRENT_DATE - INTERVAL 12 MONTH)
)
SELECT
    pernr                                          AS pernr,          -- shadowed (FK)
    absence_type                                   AS absence_type,
    ROUND(SUM(absence_days_workable), 2)           AS total_days_workable,
    ROUND(SUM(absence_days_calendar), 2)           AS total_days_calendar,
    COUNT(*)                                       AS absence_records
FROM absences
GROUP BY pernr, absence_type
ORDER BY pernr, absence_type
$seed$, $seed$Días de ausencia por empleado y tipo en los últimos 12 meses. Una fila por (pernr, tipo de ausencia).$seed$, $seed${}$seed$::jsonb, $seed$$seed$, NOW(), (SELECT id FROM workspaces ORDER BY created_at ASC LIMIT 1)),
($seed$absence_by_type_and_month$seed$, $seed$gold$seed$, $seed$sap_hcm$seed$, $seed$["raw/sap_hcm/LeaveAbsence"]$seed$::jsonb, $seed$
SELECT
    CAST(DATE_TRUNC('month', CAST(valid_from AS DATE)) AS DATE) AS absence_month,
    absence_type                                               AS absence_type,
    ROUND(SUM(absence_days_workable), 2)                       AS total_days_workable,
    COUNT(DISTINCT pernr)                                      AS employees_affected,
    COUNT(*)                                                   AS absence_records
FROM read_parquet('s3://{bucket}/silver/sap_hcm/sap_hcm_leaveabsence_latest/**/*.parquet')
WHERE valid_from IS NOT NULL
GROUP BY 1, 2
ORDER BY absence_month DESC, absence_type
$seed$, $seed$Tendencia mensual de ausencias por tipo: total de días hábiles y empleados distintos afectados.$seed$, $seed${}$seed$::jsonb, $seed$$seed$, NOW(), (SELECT id FROM workspaces ORDER BY created_at ASC LIMIT 1)),
($seed$manager_hierarchy$seed$, $seed$gold$seed$, $seed$sap_hcm$seed$, $seed$["raw/sap_hcm/EmployeeMaster", "raw/sap_hcm/PersonalData", "raw/sap_hcm/ContractData"]$seed$::jsonb, $seed$
-- TODO: poblar manager_pernr cuando exista la fuente del jefe:
--   (a) HRP1001Set relación A002 (posición -> posición padre) -> manager por posición, o
--   (b) PA0001.Sbrtr (clave de supervisor) si se añade a select_fields.
-- Con esa arista, sustituir por un WITH RECURSIVE para profundidad y conteo de reportes.
WITH emp AS (
    SELECT pernr, full_name, org_unit_id
    FROM read_parquet('s3://{bucket}/silver/sap_hcm/sap_hcm_employee_master_full/**/*.parquet')
    WHERE is_active = TRUE
)
SELECT
    pernr                            AS pernr,           -- shadowed (FK)
    full_name                        AS full_name,       -- masked
    org_unit_id                      AS org_unit_id,
    CAST(NULL AS VARCHAR)            AS manager_pernr,
    CAST(NULL AS VARCHAR)            AS manager_name,
    0                                AS direct_reports,
    0                                AS depth
FROM emp
ORDER BY pernr
$seed$, $seed$Árbol de supervisión por empleado. El vínculo manager no está disponible en el bronze actual (PA0001 extraído no incluye Sbrtr y no se extrae HRP1001); se entrega cada empleado activo con manager NULL hasta habilitar esa fuente.$seed$, $seed${}$seed$::jsonb, $seed$$seed$, NOW(), (SELECT id FROM workspaces ORDER BY created_at ASC LIMIT 1)),
($seed$employees_anomalies$seed$, $seed$gold$seed$, $seed$sap_hcm$seed$, $seed$["raw/sap_hcm/EmployeeMaster", "raw/sap_hcm/PersonalData", "raw/sap_hcm/ContractData", "raw/sap_hcm/EmployeeActions"]$seed$::jsonb, $seed$
-- Mejora propia (no existe en Replicon): precomputa anomalías para el motor de decisiones.
-- Casos cubiertos hoy: sin centro de costo, sin posición, activo con última acción de baja.
-- Pendiente (requiere fuente de jefe): manager inválido -> ver manager_hierarchy TODO.
WITH base AS (
    SELECT pernr, full_name, org_unit_id, position_id, cost_center
    FROM read_parquet('s3://{bucket}/silver/sap_hcm/sap_hcm_employee_master_full/**/*.parquet')
    WHERE is_active = TRUE
),
last_action AS (
    -- Acción más reciente por empleado (PA0000). Massn 02/10 = baja en catálogos estándar.
    SELECT pernr, action_type,
           ROW_NUMBER() OVER (PARTITION BY pernr ORDER BY valid_from DESC) AS rn
    FROM read_parquet('s3://{bucket}/silver/sap_hcm/sap_hcm_employeeactions_latest/**/*.parquet')
),
no_cost_center AS (
    SELECT pernr, full_name,
           'missing_cost_center' AS anomaly_type,
           'medium'              AS severity,
           '{"reason":"empleado activo sin centro de costo"}' AS details
    FROM base
    WHERE cost_center IS NULL OR TRIM(cost_center) = ''
),
no_position AS (
    SELECT pernr, full_name,
           'missing_position' AS anomaly_type,
           'medium'           AS severity,
           '{"reason":"empleado activo sin posicion asignada"}' AS details
    FROM base
    WHERE position_id IS NULL OR TRIM(position_id) = ''
),
terminated_but_active AS (
    SELECT b.pernr, b.full_name,
           'terminated_but_active' AS anomaly_type,
           'high'                  AS severity,
           '{"reason":"ultima accion es baja pero sigue activo"}' AS details
    FROM base b
    JOIN last_action la ON la.pernr = b.pernr AND la.rn = 1
    WHERE la.action_type IN ('02', '10')
)
SELECT pernr, full_name, anomaly_type, severity, details,
       CURRENT_TIMESTAMP AS detected_at
FROM (
    SELECT * FROM no_cost_center
    UNION ALL
    SELECT * FROM no_position
    UNION ALL
    SELECT * FROM terminated_but_active
) anomalies
ORDER BY severity, anomaly_type, pernr
$seed$, $seed$Detección automática de irregularidades operativas sobre empleados activos (preparación Fase 3). UNION de varios casos con tipo, severidad y detalle.$seed$, $seed${}$seed$::jsonb, $seed$$seed$, NOW(), (SELECT id FROM workspaces ORDER BY created_at ASC LIMIT 1)),
($seed$workforce_cost_monthly$seed$, $seed$gold$seed$, $seed$sap_hcm$seed$, $seed$["raw/sap_hcm/EmployeeMaster", "raw/sap_hcm/OrgUnit"]$seed$::jsonb, $seed$
-- TODO: requiere extracción de PA0008 (BasicPay) que aún no está habilitada.
-- Cuando exista raw/sap_hcm/BasicPay (PA0008.Betrg = importe, Lga = clase de
-- pago), unir por pernr y rango de fechas y reemplazar el NULL por:
--   ROUND(SUM(CAST(bp.Betrg AS DECIMAL(14,2))), 2) AS total_base_salary
-- agregando por mes/org/centro de costo.
WITH emp AS (
    SELECT org_unit_id, org_unit_name, cost_center
    FROM read_parquet('s3://{bucket}/silver/sap_hcm/sap_hcm_employee_master_full/**/*.parquet')
    WHERE is_active = TRUE
)
SELECT
    CAST(DATE_TRUNC('month', CURRENT_DATE) AS DATE) AS cost_month,
    org_unit_id                                     AS org_unit_id,
    COALESCE(org_unit_name, '(sin nombre)')         AS org_unit_name,
    COALESCE(cost_center, '(sin centro)')           AS cost_center,
    COUNT(*)                                        AS active_headcount,
    CAST(NULL AS DECIMAL(14,2))                     AS total_base_salary  -- TODO: PA0008
FROM emp
GROUP BY org_unit_id, org_unit_name, cost_center
ORDER BY org_unit_id, cost_center
$seed$, $seed$Costo de nómina estimado por mes / unidad org / centro de costo. PENDIENTE: requiere extracción de PA0008 (BasicPay), no habilitada en Bloque A. Hoy entrega la dimensión con headcount y costo en NULL.$seed$, $seed${}$seed$::jsonb, $seed$$seed$, NOW(), (SELECT id FROM workspaces ORDER BY created_at ASC LIMIT 1))
ON CONFLICT (name) DO NOTHING;

INSERT INTO schema_migrations (filename, applied_at)
VALUES ('80_sap_hcm_datasets_seed.sql', NOW())
ON CONFLICT (filename) DO NOTHING;
