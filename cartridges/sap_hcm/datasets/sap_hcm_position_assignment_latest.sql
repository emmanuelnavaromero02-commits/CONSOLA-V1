-- sap_hcm_position_assignment_latest  (silver)  cartridge: sap_hcm
-- sources: ["raw/sap_hcm/Position", "raw/sap_hcm/EmployeeMaster", "raw/sap_hcm/PersonalData"]
-- description: Posiciones con su titular actual (si existe). Marca posiciones vacantes (sin empleado asignado vía Plans).

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
