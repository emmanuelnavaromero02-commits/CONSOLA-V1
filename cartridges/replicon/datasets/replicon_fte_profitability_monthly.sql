-- replicon_fte_profitability_monthly  (gold)  cartridge: replicon
-- sources: []
-- description: Análisis de productividad mes a mes

WITH

-- 1. Catálogo de empleados
empleados AS (
    SELECT
        LOWER(TRIM(usuario))                                    AS usuario_key,
        TRIM(usuario)                                           AS consultor,
        COALESCE(NULLIF(TRIM(supervisor_nombre), ''), 'N/D')   AS supervisor,
        COALESCE(NULLIF(TRIM(departamento), ''), 'N/D')        AS departamento,
        COALESCE(tipo_empleado, 'Unknown')                     AS tipo_empleado,
        COALESCE(costo_hora, 0)                                AS costo_hora
    FROM read_parquet('s3://{bucket}/silver/replicon/empleados_maestro/data.parquet')
),

-- 2. Tarifa de facturación
billing_rates AS (
    SELECT userid, projectcode, billing_rate_usd
    FROM read_parquet('s3://{bucket}/silver/replicon/replicon_resourceallocation_latest/data.parquet')
),

-- 3. Revenue manager por proyecto
proj_rm AS (
    SELECT project_code, project_name, revenue_manager FROM (
        SELECT
            "Project Code"                                      AS project_code,
            "Project Name"                                      AS project_name,
            COALESCE(NULLIF(TRIM("Revenue Manager"), ''), 'N/D') AS revenue_manager,
            ROW_NUMBER() OVER (
                PARTITION BY "Project Code"
                ORDER BY "FechaReporte" DESC NULLS LAST
            ) AS rn
        FROM read_parquet('s3://{bucket}/silver/replicon/replicon_project_detail_curated/data.parquet')
    ) WHERE rn = 1
),

-- 4. BillingItem
billing_items AS (
    SELECT
        DATE_TRUNC('month', TRY_CAST(entrydate AS DATE))       AS mes,
        username,
        projectcode,
        SUM(COALESCE(billableamountbasecurrency, 0))           AS revenue_bi
    FROM read_parquet('s3://{bucket}/silver/replicon/replicon_billingitem_latest/data.parquet')
    WHERE isbillable = true AND entrydate IS NOT NULL
    GROUP BY 1, 2, 3
),

-- 5. TimeEntry enriquecido
te AS (
    SELECT
        DATE_TRUNC('month', TRY_CAST(te.entrydate AS DATE))    AS mes,
        te.username,
        te.userid,
        te.projectcode,
        te.clientname,
        te.durationhours,
        te.isbillable,
        COALESCE(br.billing_rate_usd, 0)                       AS billing_rate_usd,
        COALESCE(em.costo_hora, 0)                             AS costo_hora,
        COALESCE(em.supervisor, 'N/D')                         AS supervisor,
        COALESCE(em.departamento, 'N/D')                       AS departamento,
        COALESCE(em.tipo_empleado, 'Unknown')                  AS tipo_empleado,
        COALESCE(em.consultor, te.username)                    AS consultor_nombre
    FROM read_parquet('s3://{bucket}/silver/replicon/replicon_timeentry_latest/data.parquet') te
    LEFT JOIN billing_rates br
        ON br.userid = te.userid AND br.projectcode = te.projectcode
    LEFT JOIN empleados em
        ON em.usuario_key = LOWER(TRIM(te.username))
),

-- 6. Costo hundido a nivel empleado/mes
hundido_emp AS (
    SELECT
        username, mes, costo_hora,
        SUM(CASE WHEN isbillable THEN durationhours ELSE 0 END) AS hrs_fact_total,
        SUM(durationhours)                                      AS hrs_total,
        GREATEST(0, 168.0 - SUM(durationhours)) * costo_hora   AS costo_hundido
    FROM te
    WHERE mes IS NOT NULL
      AND tipo_empleado = 'Employee'
      AND costo_hora > 0
    GROUP BY username, mes, costo_hora
),

-- 7. Peso de horas facturables
fact_pesos AS (
    SELECT
        username, mes, projectcode,
        SUM(CASE WHEN isbillable THEN durationhours ELSE 0 END) AS hrs_fact
    FROM te
    WHERE mes IS NOT NULL
    GROUP BY username, mes, projectcode
),

-- 8. Costo hundido prorrateado
hundido_proj AS (
    SELECT
        fp.mes, fp.username, fp.projectcode,
        SUM(fp.hrs_fact / NULLIF(he.hrs_fact_total, 0) * he.costo_hundido) AS costo_hundido
    FROM fact_pesos fp
    JOIN hundido_emp he
        ON he.username = fp.username AND he.mes = fp.mes
    GROUP BY fp.mes, fp.username, fp.projectcode
),

-- 9. Agregación principal
te_agg AS (
    SELECT
        mes,
        username,
        MAX(consultor_nombre)                                  AS consultor,
        MAX(supervisor)                                        AS supervisor,
        MAX(departamento)                                      AS departamento,
        MAX(tipo_empleado)                                     AS tipo_empleado,
        projectcode,
        MAX(clientname)                                        AS cliente,
        MAX(billing_rate_usd)                                  AS billing_rate_usd,
        MAX(costo_hora)                                        AS costo_hora,
        SUM(durationhours)                                     AS horas_ejecutadas,
        SUM(CASE WHEN isbillable  THEN durationhours ELSE 0 END) AS horas_facturables,
        SUM(CASE WHEN NOT isbillable THEN durationhours ELSE 0 END) AS horas_no_facturables,
        SUM(CASE WHEN isbillable  THEN durationhours * billing_rate_usd ELSE 0 END) AS revenue_tarifa,
        SUM(CASE WHEN isbillable  THEN durationhours * costo_hora ELSE 0 END) AS costo_directo,
        SUM(CASE WHEN NOT isbillable THEN durationhours * costo_hora ELSE 0 END) AS costo_no_facturable
    FROM te
    WHERE mes IS NOT NULL
    GROUP BY mes, username, projectcode
)

SELECT
    ta.mes,
    COALESCE(NULLIF(ta.supervisor,'N/D'), pr.revenue_manager, 'N/D') AS revenue_manager,
    ta.departamento,
    ta.consultor,
    ta.tipo_empleado,
    ta.projectcode                                             AS proyecto,
    COALESCE(pr.project_name, '')                              AS project_name,
    ta.cliente,
    ROUND(ta.horas_ejecutadas, 2)                              AS horas_ejecutadas,
    ROUND(ta.horas_facturables, 2)                             AS horas_facturables,
    ROUND(ta.horas_no_facturables, 2)                          AS horas_no_facturables,
    ROUND(GREATEST(0, 168.0 - ta.horas_ejecutadas), 2)        AS horas_hundidas_capacidad,
    ta.billing_rate_usd,
    ta.costo_hora,
    ROUND(ta.costo_directo, 2)                                 AS costo_directo,
    ROUND(ta.costo_no_facturable, 2)                           AS costo_no_facturable,
    ROUND(COALESCE(hp.costo_hundido, 0), 2)                   AS costo_hundido,
    ROUND(ta.costo_directo + ta.costo_no_facturable + COALESCE(hp.costo_hundido, 0), 2) AS costo_total,
    ROUND(CASE WHEN ta.revenue_tarifa > 0 THEN ta.revenue_tarifa ELSE COALESCE(bi.revenue_bi, 0) END, 2) AS revenue_generado
FROM te_agg ta
LEFT JOIN hundido_proj hp
    ON hp.username = ta.username AND hp.projectcode = ta.projectcode AND hp.mes = ta.mes
LEFT JOIN proj_rm pr
    ON pr.project_code = ta.projectcode
LEFT JOIN billing_items bi
    ON bi.username = ta.username AND bi.projectcode = ta.projectcode AND bi.mes = ta.mes
