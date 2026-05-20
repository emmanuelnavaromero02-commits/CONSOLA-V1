-- pnl_detalle_consultor  (gold)  cartridge: replicon
-- description: P&L mensual prorrateado al consultor. Revenue por tipo: FPP=avance*contrato; T&M/AMS OnDemand=horas×tarifa (fallback BillingItem); Iguala/AMS Baseline/BPO/CFDI-Timbrado=BillingItem del proyecto-mes × share de horas facturables del consultor. Costo hundido prorrateado por peso de horas facturables del consultor.
-- exported from AWS postgres on session

WITH

-- 1. Catalogo proyectos
proj_catalog AS (
    SELECT project_code, project_name, revenue_manager, project_type, pct_avance_real FROM (
        SELECT
            "Project Code"                                                  AS project_code,
            "Project Name"                                                  AS project_name,
            COALESCE(NULLIF(TRIM("Revenue Manager"), ''), 'N/D')            AS revenue_manager,
            "Project Type"                                                  AS project_type,
            COALESCE("% Real Progress", 0)                                  AS pct_avance_real,
            ROW_NUMBER() OVER (PARTITION BY "Project Code" ORDER BY "FechaReporte" DESC NULLS LAST) AS rn
        FROM read_parquet('s3://{bucket}/silver/replicon/replicon_project_detail_curated/data.parquet')
    ) WHERE rn = 1
),

-- 2. Valor de contrato (FPP)
proj_contract AS (
    SELECT project_code, cliente, valor_contrato FROM (
        SELECT
            code                                                            AS project_code,
            clientname                                                      AS cliente,
            CASE WHEN projectcurrencyid = 8
                 THEN COALESCE(totalestimatedcontractamount, 0) / 20.0
                 ELSE COALESCE(totalestimatedcontractamount, 0)
            END                                                             AS valor_contrato,
            ROW_NUMBER() OVER (PARTITION BY code ORDER BY load_date DESC NULLS LAST) AS rn
        FROM read_parquet('s3://{bucket}/silver/replicon/replicon_project_latest/data.parquet')
    ) WHERE rn = 1
),

-- 3. Tarifa por usuario+proyecto — dedup a 1 fila por (userid, projectcode)
billing_rates AS (
    SELECT userid, projectcode, MAX(billing_rate_usd) AS billing_rate_usd
    FROM read_parquet('s3://{bucket}/silver/replicon/replicon_resourceallocation_latest/data.parquet')
    GROUP BY userid, projectcode
),

-- 4. Revenue desde BillingItem
--   4a. Por proyecto-mes-consultor (fallback T&M / AMS OnDemand)
billing_items_user AS (
    SELECT
        DATE_TRUNC('month', CAST(entrydate AS DATE))                        AS mes,
        projectcode,
        username,
        SUM(COALESCE(billableamountbasecurrency, 0))                       AS revenue_bi
    FROM read_parquet('s3://{bucket}/silver/replicon/replicon_billingitem_latest/data.parquet')
    WHERE isbillable = true AND entrydate IS NOT NULL
    GROUP BY 1, 2, 3
),

--   4b. Por proyecto-mes (Iguala / AMS Baseline / BPO / CFDI-Timbrado)
billing_items_proj AS (
    SELECT
        DATE_TRUNC('month', CAST(entrydate AS DATE))                        AS mes,
        projectcode,
        SUM(COALESCE(billableamountbasecurrency, 0))                       AS revenue_bi
    FROM read_parquet('s3://{bucket}/silver/replicon/replicon_billingitem_latest/data.parquet')
    WHERE isbillable = true AND entrydate IS NOT NULL
    GROUP BY 1, 2
),

-- 5. Revenue FPP: avance_último_mes_actual − avance_último_mes_anterior × contrato
fpp_monthly_last AS (
    SELECT mes, project_code, real_progress AS last_progress
    FROM (
        SELECT
            DATE_TRUNC('month', CAST(modified_at AS DATE))                AS mes,
            project_code,
            real_progress,
            ROW_NUMBER() OVER (
                PARTITION BY project_code, DATE_TRUNC('month', CAST(modified_at AS DATE))
                ORDER BY modified_at DESC
            ) AS rn
        FROM read_parquet('s3://{bucket}/silver/replicon/project_progress_history/data.parquet')
        WHERE real_progress IS NOT NULL
    ) WHERE rn = 1
),

fpp_revenue AS (
    SELECT
        m.mes, m.project_code,
        (m.last_progress - COALESCE(LAG(m.last_progress) OVER (
            PARTITION BY m.project_code ORDER BY m.mes
        ), 0)) / 100.0 * COALESCE(pc.valor_contrato, 0)                    AS revenue_usd
    FROM fpp_monthly_last m
    LEFT JOIN proj_contract pc ON pc.project_code = m.project_code
),

-- 6. Facturación por mes-proyecto (AMS Base Line / BPO)
facturacion AS (
    SELECT
        DATE_TRUNC('month', CAST("Fecha" AS DATE))                         AS mes,
        CAST(TRY_CAST(TRY_CAST("Project Code" AS DOUBLE) AS BIGINT) AS VARCHAR) AS project_code,
        SUM(CASE WHEN "Moneda" = 'MXN' THEN "Subtotal" / 20.0
                 ELSE "Subtotal" END)                                      AS facturacion_mes_usd
    FROM read_parquet('s3://{bucket}/silver/replicon/replicon_projectbilling_curated/data.parquet')
    WHERE "Fecha" IS NOT NULL AND "Project Code" != '0'
    GROUP BY 1, 2
),

-- 7. TimeEntry enriquecido por consultor
te_enriched AS (
    SELECT
        te.username,
        te.userid,
        te.projectcode,
        te.clientname,
        DATE_TRUNC('month', TRY_CAST(te.entrydate AS DATE))                AS mes,
        te.durationhours,
        te.isbillable,
        COALESCE(br.billing_rate_usd, 0)                                   AS billing_rate_usd,
        COALESCE(em.costo_hora, 0)                                         AS costo_hora,
        COALESCE(em.tipo_empleado, 'Unknown')                              AS tipo_empleado
    FROM read_parquet('s3://{bucket}/silver/replicon/replicon_timeentry_latest/data.parquet') te
    LEFT JOIN billing_rates br
        ON br.userid = te.userid AND br.projectcode = te.projectcode
    LEFT JOIN read_parquet('s3://{bucket}/silver/replicon/empleados_maestro/data.parquet') em
        ON LOWER(TRIM(em.usuario)) = LOWER(TRIM(te.username))
),

-- 8. Costo hundido por empleado/mes
hundido_emp AS (
    SELECT
        username, mes, costo_hora,
        SUM(CASE WHEN isbillable THEN durationhours ELSE 0 END)            AS hrs_fact_total,
        GREATEST(0, 168.0 - SUM(durationhours)) * costo_hora              AS costo_hundido_total
    FROM te_enriched
    WHERE mes IS NOT NULL AND tipo_empleado = 'Employee' AND costo_hora > 0
    GROUP BY username, mes, costo_hora
),

-- 9. Métricas TE por (mes, proyecto, consultor)
te_agg AS (
    SELECT
        mes,
        projectcode                                                         AS project_code,
        username                                                            AS consultor,
        MAX(clientname)                                                     AS cliente_te,
        MAX(billing_rate_usd)                                               AS billing_rate_usd,
        MAX(costo_hora)                                                     AS costo_hora,
        MAX(tipo_empleado)                                                  AS tipo_empleado,
        SUM(CASE WHEN isbillable     THEN durationhours ELSE 0 END)        AS horas_facturables,
        SUM(CASE WHEN NOT isbillable THEN durationhours ELSE 0 END)        AS horas_no_facturables,
        SUM(durationhours)                                                  AS horas_totales,
        SUM(CASE WHEN isbillable THEN durationhours * billing_rate_usd ELSE 0 END) AS revenue_tarifa_usr,
        SUM(CASE WHEN isbillable THEN durationhours * costo_hora ELSE 0 END) AS costo_directo
    FROM te_enriched
    WHERE mes IS NOT NULL
    GROUP BY mes, project_code, consultor
),

-- 10. Total horas facturables por (mes, proyecto) — para prorratear revenue del proyecto a consultores
proj_hrs AS (
    SELECT mes, project_code,
           SUM(horas_facturables) AS hrs_fact_proyecto
    FROM te_agg
    GROUP BY mes, project_code
)

SELECT
    ta.mes,
    COALESCE(pc.revenue_manager, 'N/D')                                    AS revenue_manager,
    COALESCE(pc2.cliente, ta.cliente_te, 'N/D')                            AS cliente,
    ta.project_code                                                         AS proyecto,
    COALESCE(pc.project_name, '')                                          AS project_name,
    COALESCE(pc.project_type, 'N/D')                                       AS tipo_proyecto,
    ta.consultor,
    ta.tipo_empleado,
    ROUND(ta.horas_facturables, 2)                                         AS horas_facturables,
    ROUND(ta.horas_no_facturables, 2)                                      AS horas_no_facturables,
    ROUND(ta.horas_totales, 2)                                             AS horas_totales,
    ROUND(ta.billing_rate_usd, 2)                                          AS billing_rate_usd,
    ROUND(ta.costo_hora, 2)                                                AS costo_hora,
    -- COSTO DIRECTO (consultor)
    ROUND(ta.costo_directo, 2)                                             AS costo_directo,
    -- COSTO HUNDIDO atribuido al consultor en este proyecto:
    --   peso = horas_fact del consultor en este proyecto / total horas_fact del consultor en el mes
    ROUND(
        CASE WHEN he.hrs_fact_total > 0
             THEN ta.horas_facturables / he.hrs_fact_total * he.costo_hundido_total
             ELSE 0 END, 2)                                                AS costo_hundido_aporte,
    -- REVENUE atribuido al consultor:
    --   T&M / AMS OnDemand                        → su propia hrs × tarifa (fallback BillingItem)
    --   FPP                                       → revenue FPP del proyecto × share de su horas_fact
    --   Iguala / AMS Baseline / BPO / CFDI-Timbrado → BillingItem del proyecto-mes × share de horas_fact
    ROUND(
        CASE
            WHEN pc.project_type IN ('T&M', 'AMS (On Demand)')
              THEN CASE WHEN ta.revenue_tarifa_usr > 0 THEN ta.revenue_tarifa_usr
                        ELSE COALESCE(biu.revenue_bi, 0) END
            WHEN pc.project_type = 'FPP'
              THEN CASE WHEN ph.hrs_fact_proyecto > 0
                        THEN ta.horas_facturables / ph.hrs_fact_proyecto
                             * COALESCE(fr.revenue_usd, 0)
                        ELSE 0 END
            WHEN pc.project_type IN (
                    'Iguala',
                    'AMS Baseline', 'AMS (Base Line)',
                    'BPO',
                    'CFDI - Timbrado', 'CFDI-Timbrado'
                 )
              THEN CASE WHEN ph.hrs_fact_proyecto > 0
                        THEN ta.horas_facturables / ph.hrs_fact_proyecto
                             * COALESCE(bip.revenue_bi, 0)
                        ELSE 0 END
            ELSE CASE WHEN ta.revenue_tarifa_usr > 0 THEN ta.revenue_tarifa_usr
                      ELSE COALESCE(biu.revenue_bi, 0) END
        END, 2)                                                            AS revenue_aporte,
    -- Totales del proyecto-mes para contexto
    ROUND(COALESCE(fr.revenue_usd, 0), 2)                                  AS proj_revenue_fpp_mes,
    ROUND(COALESCE(fac.facturacion_mes_usd, 0), 2)                         AS proj_facturacion_mes
FROM te_agg ta
LEFT JOIN proj_catalog pc   ON pc.project_code  = ta.project_code
LEFT JOIN proj_contract pc2 ON pc2.project_code = ta.project_code
LEFT JOIN proj_hrs ph       ON ph.project_code  = ta.project_code AND ph.mes = ta.mes
LEFT JOIN fpp_revenue fr    ON fr.project_code  = ta.project_code AND fr.mes = ta.mes
LEFT JOIN facturacion fac   ON fac.project_code = ta.project_code AND fac.mes = ta.mes
LEFT JOIN billing_items_user biu
    ON biu.projectcode = ta.project_code AND biu.mes = ta.mes AND biu.username = ta.consultor
LEFT JOIN billing_items_proj bip
    ON bip.projectcode = ta.project_code AND bip.mes = ta.mes
LEFT JOIN hundido_emp he ON he.username = ta.consultor AND he.mes = ta.mes
ORDER BY ta.mes DESC, revenue_manager, ta.project_code, ta.consultor

