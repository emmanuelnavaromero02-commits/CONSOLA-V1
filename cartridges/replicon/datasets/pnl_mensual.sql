-- pnl_mensual  (gold)  cartridge: replicon
-- description: P&L mensual por Revenue Manager/Cliente/Proyecto. Revenue por tipo: FPP=avance*contrato; T&M/AMS On Demand=horas*tarifa (fallback BillingItem); Iguala/AMS Baseline/BPO/CFDI-Timbrado=BillingItem del mes. Costo=directo+hundido. WIP=revenue-facturacion.
-- exported from AWS postgres on session

WITH

-- 1. Catalogo proyectos: tipo, revenue manager (project_detail_curated deduplicado)
proj_catalog AS (
    SELECT project_code, project_name, revenue_manager, project_type, pct_avance_real FROM (
        SELECT
            "Project Code"                                                   AS project_code,
            "Project Name"                                                   AS project_name,
            COALESCE(NULLIF(TRIM("Revenue Manager"), ''), 'N/D')            AS revenue_manager,
            "Project Type"                                                   AS project_type,
            COALESCE("% Real Progress", 0)                                   AS pct_avance_real,
            ROW_NUMBER() OVER (PARTITION BY "Project Code" ORDER BY "FechaReporte" DESC NULLS LAST) AS rn
        FROM read_parquet('s3://{bucket}/silver/replicon/replicon_project_detail_curated/data.parquet')
    ) WHERE rn = 1
),

-- 2. Valor de contrato (FPP revenue base) desde project_latest
--    projectcurrencyid=1 â†’ USD, projectcurrencyid=8 â†’ MXN (Ã·20)
proj_contract AS (
    SELECT project_code, cliente, valor_contrato FROM (
        SELECT
            code                                                             AS project_code,
            clientname                                                       AS cliente,
            CASE WHEN projectcurrencyid = 8
                 THEN COALESCE(totalestimatedcontractamount, 0) / 20.0
                 ELSE COALESCE(totalestimatedcontractamount, 0)
            END                                                              AS valor_contrato,
            ROW_NUMBER() OVER (PARTITION BY code ORDER BY load_date DESC NULLS LAST) AS rn
        FROM read_parquet('s3://{bucket}/silver/replicon/replicon_project_latest/data.parquet')
    ) WHERE rn = 1
),

-- 3. Tarifas de facturacion por usuario+proyecto (T&M, AMS On Demand) — dedup a 1 fila por (userid, projectcode)
billing_rates AS (
    SELECT userid, projectcode, MAX(billing_rate_usd) AS billing_rate_usd
    FROM read_parquet('s3://{bucket}/silver/replicon/replicon_resourceallocation_latest/data.parquet')
    GROUP BY userid, projectcode
),

-- 4. Revenue T&M desde BillingItem (fallback cuando billing_rate_usd = 0)
--    billableamountbasecurrency ya viene calculado por Replicon (horas Ã— tarifa real)
billing_items AS (
    SELECT
        DATE_TRUNC('month', CAST(entrydate AS DATE))                        AS mes,
        projectcode,
        SUM(COALESCE(billableamountbasecurrency, 0))                       AS revenue_usd
    FROM read_parquet('s3://{bucket}/silver/replicon/replicon_billingitem_latest/data.parquet')
    WHERE isbillable = true AND entrydate IS NOT NULL
    GROUP BY 1, 2
),

-- 5. Revenue FPP: avance_último_mes_actual − avance_último_mes_anterior.
--    (mes anterior = el último mes con dato del proyecto, no necesariamente consecutivo).
--    Si el delta es negativo se contabiliza (revenue negativo).
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

-- 5. Facturacion en USD por proyecto/mes
--    Project Code en billing viene como float string ("29630595633.0") â€” normalizar a entero
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

-- 6. TimeEntry enriquecido: tarifa facturacion y costo/hora por usuario+proyecto
te_enriched AS (
    SELECT
        te.username,
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

-- 7a. Costo hundido por empleado/mes:
--     MAX(0, 168 - horas_totales_registradas) Ã— costo/hora
--     Ocioso puro: tiempo no registrado. costo_no_facturable no se duplica.
--     Solo Employees (no Contractors). MÃ­nimo 0.
hundido_empleado AS (
    SELECT
        username,
        mes,
        costo_hora,
        SUM(CASE WHEN isbillable THEN durationhours ELSE 0 END)            AS hrs_fact_total,
        GREATEST(0, 168.0
            - SUM(durationhours))
            * costo_hora                                                    AS costo_hundido
    FROM te_enriched
    WHERE mes IS NOT NULL AND tipo_empleado = 'Employee' AND costo_hora > 0
    GROUP BY username, mes, costo_hora
),

-- 7b. Horas facturables por empleado+proyecto+mes (pesos del prorrateo)
fact_pesos AS (
    SELECT
        username,
        mes,
        projectcode,
        SUM(durationhours)                                                  AS hrs_fact
    FROM te_enriched
    WHERE isbillable = true AND mes IS NOT NULL
    GROUP BY username, mes, projectcode
),

-- 7c. Costo hundido prorrateado a cada proyecto segÃºn peso de horas facturables
hundido_prorateado AS (
    SELECT
        fp.mes,
        fp.projectcode,
        SUM(fp.hrs_fact / NULLIF(he.hrs_fact_total, 0)
            * he.costo_hundido)                                            AS costo_hundido
    FROM fact_pesos fp
    JOIN hundido_empleado he
        ON he.username = fp.username AND he.mes = fp.mes
    GROUP BY fp.mes, fp.projectcode
),

-- 7. MÃ©tricas de tiempo por proyecto/mes (sin costo_hundido â€” viene de hundido_prorateado)
te_agg AS (
    SELECT
        mes,
        projectcode                                                         AS project_code,
        MAX(clientname)                                                     AS cliente_te,
        SUM(CASE WHEN isbillable THEN durationhours ELSE 0 END)            AS horas_facturables,
        SUM(durationhours)                                                  AS horas_totales,
        SUM(CASE WHEN isbillable
                 THEN durationhours * billing_rate_usd ELSE 0 END)         AS revenue_tarifa,
        SUM(CASE WHEN isbillable
                 THEN durationhours * costo_hora ELSE 0 END)               AS costo_directo
    FROM te_enriched
    WHERE mes IS NOT NULL
    GROUP BY mes, project_code
),

-- 8. P&L base con revenue por tipo de proyecto
pnl_base AS (
    SELECT
        ta.mes,
        COALESCE(pc.revenue_manager, 'N/D')                                AS revenue_manager,
        COALESCE(pc2.cliente, ta.cliente_te, 'N/D')                        AS cliente,
        ta.project_code                                                     AS proyecto,
        COALESCE(pc.project_name, '')                                      AS project_name,
        COALESCE(pc.project_type, 'N/D')                                   AS tipo_proyecto,
        COALESCE(pc.pct_avance_real, 0)                                    AS pct_avance_real,
        CASE
            WHEN pc.project_type = 'FPP'
                THEN COALESCE(fr.revenue_usd, 0)
            WHEN pc.project_type IN ('T&M', 'AMS (On Demand)')
                THEN CASE
                    WHEN ta.revenue_tarifa > 0 THEN ta.revenue_tarifa
                    ELSE COALESCE(bi.revenue_usd, 0)   -- fallback: BillingItem cuando tarifa no estÃ¡ en ResourceAllocation
                END
            WHEN pc.project_type IN (
                    'Iguala',
                    'AMS Baseline', 'AMS (Base Line)',
                    'BPO',
                    'CFDI - Timbrado', 'CFDI-Timbrado'
                )
                THEN COALESCE(bi.revenue_usd, 0)
            ELSE CASE
                    WHEN ta.revenue_tarifa > 0 THEN ta.revenue_tarifa
                    ELSE COALESCE(bi.revenue_usd, 0)
                END
        END                                                                 AS revenue_usd,
        COALESCE(fac.facturacion_mes_usd, 0)                               AS facturacion_mes_usd,
        ta.costo_directo,
        COALESCE(hp.costo_hundido, 0)                                      AS costo_hundido,
        ta.costo_directo + COALESCE(hp.costo_hundido, 0)                   AS costo_total,
        ta.horas_facturables,
        ta.horas_totales
    FROM te_agg ta
    LEFT JOIN proj_catalog pc   ON pc.project_code  = ta.project_code
    LEFT JOIN proj_contract pc2 ON pc2.project_code = ta.project_code
    LEFT JOIN fpp_revenue fr    ON fr.project_code  = ta.project_code AND fr.mes = ta.mes
    LEFT JOIN facturacion fac   ON fac.project_code = ta.project_code AND fac.mes = ta.mes
    LEFT JOIN billing_items bi  ON bi.projectcode   = ta.project_code AND bi.mes = ta.mes
    LEFT JOIN hundido_prorateado hp ON hp.projectcode = ta.project_code AND hp.mes = ta.mes
)

SELECT
    mes,
    revenue_manager,
    cliente,
    proyecto,
    project_name,
    tipo_proyecto,
    ROUND(revenue_usd, 2)                                                  AS revenue_usd,
    ROUND(facturacion_mes_usd, 2)                                          AS facturacion_mes_usd,
    ROUND(revenue_usd - facturacion_mes_usd, 2)                            AS wip_usd,
    ROUND(costo_directo, 2)                                                AS costo_directo,
    ROUND(costo_hundido, 2)                                                AS costo_hundido,
    ROUND(costo_total, 2)                                                  AS costo_total,
    ROUND(revenue_usd - costo_total, 2)                                    AS margen_bruto_usd,
    ROUND(
        CASE WHEN revenue_usd > 0
             THEN (revenue_usd - costo_total) / revenue_usd * 100
             ELSE NULL END, 2)                                             AS margen_bruto_pct,
    ROUND(horas_facturables, 2)                                            AS horas_facturables,
    ROUND(horas_totales, 2)                                                AS horas_totales,
    ROUND(pct_avance_real, 2)                                              AS pct_avance_real
FROM pnl_base
ORDER BY mes DESC, revenue_manager, cliente, proyecto
