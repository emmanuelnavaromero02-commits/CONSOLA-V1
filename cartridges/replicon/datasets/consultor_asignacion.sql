-- consultor_asignacion  (gold)  cartridge: replicon
-- sources: ["silver/replicon/empleados_maestro", "silver/replicon/replicon_project_latest", "silver/replicon/replicon_resourceallocation_latest", "silver/replicon/replicon_timeentry_latest", "silver/replicon/replicon_user_latest"]
-- description: Asignación mensual de consultores por proyecto. Cruza ResourceAllocation (plan) con TimeEntry (ejecución), enriquecida con supervisor, departamento, tipo_empleado y tipo_de_proveedor (proveniente de UserSkills via replicon_user_latest).
-- exported from AWS postgres on session

WITH 
-- 1. Asignación como espina (todos los meses, incluyendo futuro)
alloc AS (
  SELECT
    DATE_TRUNC('month', TRY_CAST(date AS DATE)) AS mes,
    userid,
    username,
    projectid,
    projectcode,
    projectname,
    SUM(durationhours) AS horas_asignadas
  FROM read_parquet('s3://{bucket}/silver/replicon/replicon_resourceallocation_latest/**/*.parquet')
  WHERE date IS NOT NULL
  GROUP BY 1, 2, 3, 4, 5, 6
),

-- 2. Empleados con supervisor y departamento (gold dim_empleados)
empleados AS (
  SELECT
    id_empleado,
    usuario,
    LOWER(TRIM(usuario)) AS usuario_key,
    TRIM(usuario) AS consultor,
    COALESCE(NULLIF(TRIM(supervisor), ''), 'N/D') AS supervisor,
    COALESCE(NULLIF(TRIM(departamento), ''), 'N/D') AS departamento,
    COALESCE(tipo_empleado, 'Unknown') AS tipo_empleado
  FROM read_parquet('s3://{bucket}/silver/replicon/empleados_maestro/**/*.parquet')
),

-- 3. Proyectos (sin curación, directo de Silver)
proyectos AS (
  SELECT
    code AS projectcode,
    name AS project_name
  FROM read_parquet('s3://{bucket}/silver/replicon/replicon_project_latest/**/*.parquet')
),

-- 4. Horas ejecutadas desde TimeEntry (agregado mensual)
te_agg AS (
  SELECT
    DATE_TRUNC('month', TRY_CAST(entrydate AS DATE)) AS mes,
    userid,
    projectcode,
    SUM(durationhours) AS horas_ejecutadas,
    SUM(CASE WHEN isbillable THEN durationhours ELSE 0 END) AS horas_facturables,
    SUM(CASE WHEN NOT isbillable THEN durationhours ELSE 0 END) AS horas_no_facturables
  FROM read_parquet('s3://{bucket}/silver/replicon/replicon_timeentry_latest/**/*.parquet')
  WHERE entrydate IS NOT NULL
  GROUP BY 1, 2, 3
),

-- 5. Tipo de Proveedor por usuario (viene de UserSkills via replicon_user_latest)
user_provider AS (
  SELECT
    LOWER(TRIM(username)) AS usuario_key,
    COALESCE(NULLIF(TRIM(tipo_de_proveedor), ''), 'N/D') AS tipo_de_proveedor
  FROM read_parquet('s3://{bucket}/silver/replicon/replicon_user_latest/data.parquet')
)

SELECT
  al.mes AS mes,
  em.supervisor AS revenue_manager,
  em.departamento AS departamento,
  COALESCE(em.consultor, al.username) AS consultor,
  em.tipo_empleado AS tipo_empleado,
  COALESCE(up.tipo_de_proveedor, 'N/D') AS tipo_de_proveedor,
  al.projectcode AS proyecto,
  COALESCE(pr.project_name, al.projectname) AS project_name,
  ROUND(al.horas_asignadas, 2) AS horas_asignadas,
  ROUND(COALESCE(te.horas_ejecutadas, 0), 2) AS horas_ejecutadas,
  ROUND(COALESCE(te.horas_facturables, 0), 2) AS horas_facturables,
  ROUND(COALESCE(te.horas_no_facturables, 0), 2) AS horas_no_facturables,
  ROUND(al.horas_asignadas / 168.0 * 100, 1) AS pct_asignacion,
  ROUND(COALESCE(te.horas_ejecutadas, 0) / 168.0 * 100, 1) AS pct_ejecucion,
  CASE
    WHEN COALESCE(te.horas_ejecutadas, 0) = 0 THEN 'futuro'
    ELSE 'ejecutado'
  END AS estado
FROM alloc al
LEFT JOIN empleados em      ON em.usuario_key = LOWER(TRIM(al.username))
LEFT JOIN user_provider up  ON up.usuario_key = LOWER(TRIM(al.username))
LEFT JOIN proyectos pr      ON pr.projectcode = al.projectcode
LEFT JOIN te_agg te
  ON te.userid     = al.userid
  AND te.projectcode = al.projectcode
  AND te.mes         = al.mes
ORDER BY al.mes DESC, em.supervisor, al.projectcode, al.username
