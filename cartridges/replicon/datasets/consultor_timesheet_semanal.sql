-- consultor_timesheet_semanal  (gold)  cartridge: replicon
-- sources: ["silver/replicon/replicon_timeentry_latest", "silver/replicon/empleados_maestro"]
-- description: Hoja de tiempo semanal por consultor/proyecto desde silver TimeEntry

WITH

empleados AS (
    SELECT
        LOWER(TRIM(usuario))                           AS usuario_key,
        TRIM(usuario)                                  AS consultor,
        COALESCE(NULLIF(TRIM(supervisor), ''), 'N/D')  AS supervisor_e,
        COALESCE(NULLIF(TRIM(departamento), ''), 'N/D') AS departamento_e
    FROM read_parquet('s3://{bucket}/silver/replicon/empleados_maestro/data.parquet')
),

te AS (
    SELECT
        DATE_TRUNC('month', TRY_CAST(entrydate AS DATE)) AS mes,
        DATE_TRUNC('week',  TRY_CAST(entrydate AS DATE)) AS semana,
        username,
        projectcode,
        projectname,
        SUM(durationhours)                                                   AS horas_total,
        SUM(CASE WHEN isbillable     THEN durationhours ELSE 0 END)         AS horas_facturables,
        SUM(CASE WHEN NOT isbillable THEN durationhours ELSE 0 END)         AS horas_no_facturables
    FROM read_parquet('s3://{bucket}/silver/replicon/replicon_timeentry_latest/data.parquet')
    WHERE entrydate IS NOT NULL
    GROUP BY 1, 2, 3, 4, 5
)

SELECT
    te.mes,
    te.semana,
    COALESCE(em.supervisor_e, 'N/D')             AS revenue_manager,
    COALESCE(em.departamento_e, 'N/D')           AS departamento,
    COALESCE(em.consultor, te.username)          AS consultor,
    te.projectcode                               AS proyecto,
    te.projectname                               AS project_name,
    ROUND(te.horas_total, 2)                     AS horas_total,
    ROUND(te.horas_facturables, 2)               AS horas_facturables,
    ROUND(te.horas_no_facturables, 2)            AS horas_no_facturables
FROM te
LEFT JOIN empleados em ON em.usuario_key = LOWER(TRIM(te.username))

