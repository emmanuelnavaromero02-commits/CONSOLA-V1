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
