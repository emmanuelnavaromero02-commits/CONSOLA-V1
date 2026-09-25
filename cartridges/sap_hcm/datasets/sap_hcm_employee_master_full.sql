-- sap_hcm_employee_master_full  (silver)  cartridge: sap_hcm
-- sources: ["raw/sap_hcm/EmployeeMaster", "raw/sap_hcm/PersonalData", "raw/sap_hcm/ContractData", "raw/sap_hcm/OrgUnit", "raw/sap_hcm/Position", "raw/sap_hcm/CostCenter", "silver/sap_hcm/sap_hcm_contractdata_latest", "silver/sap_hcm/sap_hcm_employeemaster_latest", "silver/sap_hcm/sap_hcm_orgunit_latest", "silver/sap_hcm/sap_hcm_personaldata_latest", "silver/sap_hcm/sap_hcm_position_latest"]
-- description: Vista 360 del empleado: asignación org (PA0001) + datos personales (PA0002) + contrato (PA0016), enriquecida con nombre de unidad org y posición. Una fila por empleado (registro vigente).

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
