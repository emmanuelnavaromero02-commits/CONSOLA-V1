-- consultor_mensual  (gold)  cartridge: replicon
-- sources: ["raw/replicon/TimeEntry"]
-- description: Métricas mensuales por consultor y proyecto. Revenue Manager = supervisor del consultor (empleados_maestro), fallback a revenue_manager del proyecto. Horas ejecutadas, facturables, no facturables. Costos directo, no facturable y hundido prorrateado.

WITH

-- 1. Empleados maestro: supervisor (= revenue_manager), depto, costo/hora
empleados AS (
    SELECT
        LOWER(TRIM(usuario))                                   AS usuario_key,
        TRIM(usuario)                                          AS consultor,
        COALESCE(NULLIF(TRIM(supervisor), ''), 'N/D')          AS supervisor_e,
        COALESCE(NULLIF(TRIM(departamento), ''), 'N/D')        AS departamento_e,
        COALESCE(tipo_empleado, 'Unknown')                     AS tipo_empleado_e,
        COALESCE(costo_hora, 0)                                AS costo_hora
    FROM read_parquet('s3://{bucket}/silver/replicon/empleados_maestro/data.parquet')
),

-- 2. Allocation por (mes, consultor, proyecto). Dedup por última carga.
alloc_raw AS (
    SELECT
        DATE_TRUNC('month', CAST(date AS DATE))                AS mes,
        userid,
        username,
        projectid,
        projectcode,
        projectname,
        SUM(durationhours)                                     AS horas_asignadas,
        MAX(billing_rate_usd)                                  AS billing_rate_usd
    FROM (
        SELECT *, ROW_NUMBER() OVER (
            PARTITION BY date, userid, projectid
            ORDER BY load_date DESC
        ) AS rn
        FROM read_parquet('s3://{bucket}/silver/replicon/replicon_resourceallocation_latest/data.parquet')
    ) WHERE rn = 1
    GROUP BY 1, 2, 3, 4, 5, 6
),

-- 2b. TimeEntry por (mes, consultor, proyecto) — para detectar reportes sin asignación
te_keys AS (
    SELECT DISTINCT
        DATE_TRUNC('month', TRY_CAST(entrydate AS DATE))       AS mes,
        userid,
        username,
        projectid,
        projectcode,
        projectname
    FROM read_parquet('s3://{bucket}/silver/replicon/replicon_timeentry_latest/data.parquet')
    WHERE entrydate IS NOT NULL
),

-- 2c. Espina deduplicada por (mes, username, projectcode) — fuente unificada
spine AS (
    SELECT
        mes,
        MAX(userid)      AS userid,
        username,
        MAX(projectid)   AS projectid,
        projectcode,
        MAX(projectname) AS projectname
    FROM (
        SELECT mes, userid, username, projectid, projectcode, projectname FROM alloc_raw
        UNION ALL
        SELECT mes, userid, username, projectid, projectcode, projectname FROM te_keys
    )
    GROUP BY mes, username, projectcode
),

-- 2d. Espina enriquecida con horas asignadas (0 si solo viene de TE)
alloc AS (
    SELECT
        s.mes, s.userid, s.username, s.projectid, s.projectcode, s.projectname,
        COALESCE(a.horas_asignadas, 0) AS horas_asignadas,
        a.billing_rate_usd
    FROM spine s
    LEFT JOIN (
        SELECT mes, username, projectcode,
               SUM(horas_asignadas) AS horas_asignadas,
               MAX(billing_rate_usd) AS billing_rate_usd
        FROM alloc_raw
        GROUP BY mes, username, projectcode
    ) a ON a.mes = s.mes AND a.username = s.username AND a.projectcode = s.projectcode
),

-- 3. Catálogo de proyectos (para project_name) — dedup por code
proyectos AS (
    SELECT projectcode, MAX(project_name) AS project_name FROM (
        SELECT code AS projectcode, name AS project_name
        FROM read_parquet('s3://{bucket}/silver/replicon/replicon_project_latest/data.parquet')
    ) GROUP BY projectcode
),

-- 4. TimeEntry agregado por (mes, username, projectcode)
te_agg AS (
    SELECT
        DATE_TRUNC('month', TRY_CAST(entrydate AS DATE))       AS mes,
        username,
        projectcode,
        MAX(clientname)                                        AS cliente,
        SUM(durationhours)                                     AS horas_ejecutadas,
        SUM(CASE WHEN isbillable     THEN durationhours ELSE 0 END) AS horas_facturables,
        SUM(CASE WHEN NOT isbillable THEN durationhours ELSE 0 END) AS horas_no_facturables
    FROM read_parquet('s3://{bucket}/silver/replicon/replicon_timeentry_latest/data.parquet')
    WHERE entrydate IS NOT NULL
    GROUP BY 1, 2, 3
),

-- 5. BillingItem: revenue real + tarifa efectiva por (mes, consultor, proyecto)
billing_items AS (
    SELECT
        DATE_TRUNC('month', CAST(entrydate AS DATE))            AS mes,
        username,
        projectcode,
        SUM(COALESCE(billableamountbasecurrency, 0))            AS revenue_bi,
        SUM(COALESCE(billabledurationhours, 0))                 AS hrs_bi,
        MAX(CASE WHEN CAST(billingrate AS DOUBLE) > 0
                  THEN CAST(billingrate AS DOUBLE) END)         AS rate_bi
    FROM read_parquet('s3://{bucket}/silver/replicon/replicon_billingitem_latest/data.parquet')
    WHERE isbillable = true AND entrydate IS NOT NULL
    GROUP BY 1, 2, 3
),

-- 5b. Tarifa por proyecto (cuando no hay match consultor+proyecto, fallback al proyecto)
proj_rate AS (
    SELECT
        projectcode,
        SUM(COALESCE(billableamountbasecurrency, 0)) /
            NULLIF(SUM(COALESCE(billabledurationhours, 0)), 0) AS rate_proj
    FROM read_parquet('s3://{bucket}/silver/replicon/replicon_billingitem_latest/data.parquet')
    WHERE isbillable = true
      AND CAST(billabledurationhours AS DOUBLE) > 0
    GROUP BY 1
),

-- 6a. Asignación total por consultor/mes (sumando todos los proyectos)
asig_per_user AS (
    SELECT mes, username, SUM(horas_asignadas) AS hrs_asig_total
    FROM alloc
    GROUP BY mes, username
),

-- 6b. Horas facturables totales por consultor/mes (de timeentry)
fact_per_user AS (
    SELECT mes, username, SUM(horas_facturables) AS hrs_fact_total
    FROM te_agg
    GROUP BY mes, username
),

-- 6c. Costo hundido total por consultor/mes = capacidad no aprovechada
--     · meses pasados → 168 - facturables  (capacidad real no facturada)
--     · meses futuros → 168 - asignadas    (capacidad sin plan)
--     (sólo Employees con costo > 0)
hundido_user AS (
    SELECT
        ap.mes, ap.username, em.costo_hora,
        GREATEST(0,
          168.0 - CASE
            WHEN ap.mes < DATE_TRUNC('month', CURRENT_DATE)
              THEN COALESCE(fp.hrs_fact_total, 0)
            ELSE ap.hrs_asig_total
          END
        ) * em.costo_hora AS costo_hundido_total
    FROM asig_per_user ap
    JOIN empleados em ON em.usuario_key = LOWER(TRIM(ap.username))
    LEFT JOIN fact_per_user fp ON fp.mes = ap.mes AND fp.username = ap.username
    WHERE em.tipo_empleado_e = 'Employee' AND em.costo_hora > 0
),

-- 6c. Costo hundido prorrateado por proyecto = share de horas asignadas
hundido_proj AS (
    SELECT
        al.mes, al.username, al.projectcode,
        al.horas_asignadas / NULLIF(ap.hrs_asig_total, 0) * hu.costo_hundido_total AS costo_hundido
    FROM alloc al
    JOIN asig_per_user ap ON ap.mes = al.mes AND ap.username = al.username
    JOIN hundido_user  hu ON hu.mes = al.mes AND hu.username = al.username
)

SELECT
    al.mes                                                       AS mes,
    COALESCE(em.supervisor_e, 'N/D')                             AS revenue_manager,
    COALESCE(em.departamento_e, 'N/D')                           AS departamento,
    COALESCE(em.consultor, al.username)                          AS consultor,
    COALESCE(em.tipo_empleado_e, 'Unknown')                      AS tipo_empleado,
    al.projectcode                                               AS proyecto,
    COALESCE(pr.project_name, al.projectname, '')                AS project_name,
    te.cliente                                                   AS cliente,
    ROUND(al.horas_asignadas, 2)                                 AS horas_asignadas,
    ROUND(COALESCE(te.horas_ejecutadas, 0), 2)                   AS horas_ejecutadas,
    ROUND(COALESCE(te.horas_facturables, 0), 2)                  AS horas_facturables,
    ROUND(COALESCE(te.horas_no_facturables, 0), 2)               AS horas_no_facturables,
    ROUND(GREATEST(0, 168.0 - COALESCE(te.horas_ejecutadas, 0)), 2) AS horas_hundidas_capacidad,
    -- Tarifa: 1) BillingItem (mismo mes/consultor/proyecto), 2) tarifa del proyecto, 3) ResourceAllocation
    COALESCE(
        bi.rate_bi,
        CASE WHEN bi.hrs_bi > 0 THEN bi.revenue_bi / bi.hrs_bi END,
        pr_rate.rate_proj,
        NULLIF(al.billing_rate_usd, 0),
        0
    )                                                            AS billing_rate_usd,
    em.costo_hora                                                AS costo_hora,
    ROUND(COALESCE(te.horas_facturables, 0) * COALESCE(em.costo_hora, 0), 2) AS costo_directo,
    ROUND(COALESCE(te.horas_no_facturables, 0) * COALESCE(em.costo_hora, 0), 2) AS costo_no_facturable,
    ROUND(COALESCE(hp.costo_hundido, 0), 2)                      AS costo_hundido,
    ROUND(
        COALESCE(te.horas_facturables, 0) * COALESCE(em.costo_hora, 0)
      + COALESCE(te.horas_no_facturables, 0) * COALESCE(em.costo_hora, 0)
      + COALESCE(hp.costo_hundido, 0), 2
    )                                                            AS costo_total,
    ROUND(
      CASE
        WHEN COALESCE(bi.revenue_bi, 0) > 0
          THEN bi.revenue_bi
        ELSE COALESCE(te.horas_facturables, 0) * COALESCE(
            bi.rate_bi,
            CASE WHEN bi.hrs_bi > 0 THEN bi.revenue_bi / bi.hrs_bi END,
            pr_rate.rate_proj,
            NULLIF(al.billing_rate_usd, 0),
            0
        )
      END, 2
    )                                                            AS revenue_generado
FROM alloc al
LEFT JOIN empleados   em ON em.usuario_key = LOWER(TRIM(al.username))
LEFT JOIN proyectos   pr ON pr.projectcode = al.projectcode
LEFT JOIN te_agg      te ON te.mes = al.mes AND te.username = al.username AND te.projectcode = al.projectcode
LEFT JOIN billing_items bi ON bi.mes = al.mes AND bi.username = al.username AND bi.projectcode = al.projectcode
LEFT JOIN proj_rate pr_rate ON pr_rate.projectcode = al.projectcode
LEFT JOIN hundido_proj hp ON hp.mes = al.mes AND hp.username = al.username AND hp.projectcode = al.projectcode

