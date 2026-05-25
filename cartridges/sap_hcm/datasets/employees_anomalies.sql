-- employees_anomalies  (gold)  cartridge: sap_hcm
-- sources: ["raw/sap_hcm/EmployeeMaster", "raw/sap_hcm/PersonalData", "raw/sap_hcm/ContractData", "raw/sap_hcm/EmployeeActions"]
-- description: Detección automática de irregularidades operativas sobre empleados activos (preparación Fase 3). UNION de varios casos con tipo, severidad y detalle.

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
