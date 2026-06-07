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
