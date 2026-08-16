-- sap_successfactors_employees_anomalies  (gold)  cartridge: sap_successfactors
-- sources: ["gold/sap_successfactors/sap_successfactors_employee_360", "silver/sap_successfactors/sap_successfactors_fojobcode_latest"]
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
