-- sap_successfactors_manager_hierarchy  (gold)  cartridge: sap_successfactors
-- sources: ["raw/sap_successfactors/EmpEmployment", "raw/sap_successfactors/EmpJob", "raw/sap_successfactors/PerPersonal"]
-- description: Árbol de supervisión: cada empleado activo con su manager directo, número de reportes directos y profundidad en la jerarquía. Real gracias a EmpJob.managerId (plano).

WITH RECURSIVE emp AS (
    SELECT user_id, full_name, manager_id
    FROM read_parquet('s3://{bucket}/silver/sap_successfactors/sap_successfactors_employee_360/**/*.parquet')
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
