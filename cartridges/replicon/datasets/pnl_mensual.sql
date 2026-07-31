-- pnl_mensual  (gold)  cartridge: replicon
WITH
fx_rates AS (
    SELECT
        DATE_TRUNC('month', TRY_CAST(year_month AS DATE))                  AS mes,
        CASE WHEN COUNT(*) = 1 AND isfinite(MAX(TRY_CAST(avg_rate AS DOUBLE)))
                  AND MAX(TRY_CAST(avg_rate AS DOUBLE)) > 0
             THEN MAX(TRY_CAST(avg_rate AS DOUBLE)) END                    AS mxn_to_usd,
        'raw/fx_rates/mxn_usd/fx_rates.parquet'                            AS fx_source,
        MAX(TRY_CAST(year_month AS TIMESTAMP))                             AS fx_observed_at
    FROM read_parquet('s3://{bucket}/raw/fx_rates/mxn_usd/fx_rates.parquet')
    GROUP BY 1
),
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
proj_contract AS (
    SELECT project_code, cliente, original_contract_amount, original_currency,
           contract_amount_usd, contract_financial_status, fx_source, fx_observed_at FROM (
        SELECT
            code                                                             AS project_code,
            clientname                                                       AS cliente,
            totalestimatedcontractamount                                     AS original_contract_amount,
            CASE projectcurrencyid WHEN 1 THEN 'USD' WHEN 8 THEN 'MXN' END   AS original_currency,
            CASE WHEN projectcurrencyid = 1 THEN totalestimatedcontractamount
                 WHEN projectcurrencyid = 8 AND fx.mxn_to_usd IS NOT NULL
                 THEN totalestimatedcontractamount * fx.mxn_to_usd
            END                                                              AS contract_amount_usd,
            CASE
                WHEN totalestimatedcontractamount IS NULL THEN 'insufficient_data'
                WHEN projectcurrencyid = 1 THEN 'ready'
                WHEN projectcurrencyid = 8 AND fx.mxn_to_usd IS NOT NULL THEN 'ready'
                ELSE 'missing_fx'
            END                                                              AS contract_financial_status,
            CASE WHEN projectcurrencyid = 8 AND fx.mxn_to_usd IS NOT NULL THEN fx.fx_source END AS fx_source,
            CASE WHEN projectcurrencyid = 8 AND fx.mxn_to_usd IS NOT NULL THEN fx.fx_observed_at END AS fx_observed_at,
            ROW_NUMBER() OVER (PARTITION BY code ORDER BY load_date DESC NULLS LAST) AS rn
        FROM read_parquet('s3://{bucket}/silver/replicon/replicon_project_latest/data.parquet')
        LEFT JOIN fx_rates fx
          ON fx.mes = DATE_TRUNC('month', TRY_CAST(load_date AS DATE))
    ) WHERE rn = 1
),
billing_rates AS (
    SELECT userid, projectcode, MAX(billing_rate_usd) AS billing_rate_base_amount
    FROM read_parquet('s3://{bucket}/silver/replicon/replicon_resourceallocation_latest/data.parquet')
    GROUP BY userid, projectcode
),
billing_items AS (
    SELECT
        DATE_TRUNC('month', CAST(entrydate AS DATE))                        AS mes,
        projectcode,
        SUM(billableamountbasecurrency)                                    AS revenue_base_amount
    FROM read_parquet('s3://{bucket}/silver/replicon/replicon_billingitem_latest/data.parquet')
    WHERE isbillable = true AND entrydate IS NOT NULL
    GROUP BY 1, 2
),
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
            PARTITION BY m.project_code ORDER BY m.mes), 0)) / 100.0
            * pc.contract_amount_usd                                       AS revenue_usd,
        (m.last_progress - COALESCE(LAG(m.last_progress) OVER (
            PARTITION BY m.project_code ORDER BY m.mes), 0)) / 100.0
            * pc.original_contract_amount                                  AS revenue_original_amount,
        pc.contract_financial_status,
        pc.original_contract_amount,
        pc.original_currency,
        pc.fx_source,
        pc.fx_observed_at
    FROM fpp_monthly_last m
    LEFT JOIN proj_contract pc ON pc.project_code = m.project_code
),
billing_rows AS (
    SELECT
        DATE_TRUNC('month', CAST("Fecha" AS DATE))                         AS mes,
        CAST(TRY_CAST(TRY_CAST("Project Code" AS DOUBLE) AS BIGINT) AS VARCHAR) AS project_code,
        "Subtotal"                                                         AS original_amount,
        CASE WHEN UPPER(TRIM("Moneda")) IN ('USD', 'USD$') THEN 'USD'
             WHEN UPPER(TRIM("Moneda")) = 'MXN' THEN 'MXN' END             AS currency,
        fx.mxn_to_usd,
        fx.fx_source,
        fx.fx_observed_at
    FROM read_parquet('s3://{bucket}/silver/replicon/replicon_projectbilling_curated/data.parquet') billing
    LEFT JOIN fx_rates fx
      ON fx.mes = DATE_TRUNC('month', TRY_CAST(billing."Fecha" AS DATE))
    WHERE "Fecha" IS NOT NULL AND "Project Code" != '0'
),
facturacion AS (
    SELECT
        mes,
        project_code,
        SUM(CASE WHEN currency = 'USD' THEN original_amount END)           AS original_billing_amount_usd,
        SUM(CASE WHEN currency = 'MXN' THEN original_amount END)           AS original_billing_amount_mxn,
        CASE WHEN COUNT(DISTINCT currency) = 1 THEN MAX(currency) END      AS original_currency,
        CASE WHEN COUNT(DISTINCT currency) = 1 THEN SUM(original_amount) END AS original_billing_amount,
        CASE
            WHEN COUNT(*) FILTER (WHERE original_amount IS NULL) > 0
                THEN 'insufficient_data'
            WHEN COUNT(*) FILTER (WHERE currency IS NULL
                 OR (currency = 'MXN' AND mxn_to_usd IS NULL)) > 0 THEN 'missing_fx'
            ELSE 'ready'
        END                                                                AS billing_financial_status,
        CASE WHEN billing_financial_status = 'ready' THEN SUM(CASE
            WHEN currency = 'USD' THEN original_amount
            WHEN currency = 'MXN' THEN original_amount * mxn_to_usd END) END AS facturacion_mes_usd,
        CASE WHEN COUNT(*) FILTER (WHERE currency = 'MXN' AND mxn_to_usd IS NOT NULL) > 0
             THEN MAX(fx_source) END                                       AS fx_source,
        CASE WHEN COUNT(*) FILTER (WHERE currency = 'MXN' AND mxn_to_usd IS NOT NULL) > 0
             THEN MAX(fx_observed_at) END                                  AS fx_observed_at
    FROM billing_rows
    GROUP BY 1, 2
),
te_enriched AS (
    SELECT
        te.username,
        te.projectcode,
        te.clientname,
        DATE_TRUNC('month', TRY_CAST(te.entrydate AS DATE))                AS mes,
        te.durationhours,
        te.isbillable,
        COALESCE(br.billing_rate_base_amount, 0)                           AS billing_rate_base_amount,
        COALESCE(em.costo_hora, 0)                                         AS costo_hora,
        COALESCE(em.tipo_empleado, 'Unknown')                              AS tipo_empleado
    FROM read_parquet('s3://{bucket}/silver/replicon/replicon_timeentry_latest/data.parquet') te
    LEFT JOIN billing_rates br
        ON br.userid = te.userid AND br.projectcode = te.projectcode
    LEFT JOIN read_parquet('s3://{bucket}/silver/replicon/empleados_maestro/data.parquet') em
        ON LOWER(TRIM(em.usuario)) = LOWER(TRIM(te.username))
),
hundido_empleado AS (
    SELECT
        username,
        mes,
        costo_hora,
        SUM(CASE WHEN isbillable THEN durationhours ELSE 0 END)            AS hrs_fact_total,
        GREATEST(0, 168.0 - SUM(durationhours)) * costo_hora               AS costo_hundido
    FROM te_enriched
    WHERE mes IS NOT NULL AND tipo_empleado = 'Employee' AND costo_hora > 0
    GROUP BY username, mes, costo_hora
),
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
te_agg AS (
    SELECT
        mes,
        projectcode                                                         AS project_code,
        MAX(clientname)                                                     AS cliente_te,
        SUM(CASE WHEN isbillable THEN durationhours ELSE 0 END)            AS horas_facturables,
        SUM(durationhours)                                                  AS horas_totales,
        SUM(CASE WHEN isbillable THEN durationhours * billing_rate_base_amount
                 ELSE 0 END) AS revenue_tarifa_base,
        SUM(CASE WHEN isbillable THEN durationhours * costo_hora ELSE 0 END) AS costo_directo
    FROM te_enriched
    WHERE mes IS NOT NULL
    GROUP BY mes, project_code
),
pnl_base AS (
    SELECT
        ta.mes,
        COALESCE(pc.revenue_manager, 'N/D')                                AS revenue_manager,
        COALESCE(pc2.cliente, ta.cliente_te, 'N/D')                        AS cliente,
        ta.project_code                                                     AS proyecto,
        COALESCE(pc.project_name, '')                                      AS project_name,
        COALESCE(pc.project_type, 'N/D')                                   AS tipo_proyecto,
        COALESCE(pc.pct_avance_real, 0)                                    AS pct_avance_real,
        CASE WHEN pc.project_type = 'FPP' THEN fr.revenue_usd END          AS revenue_usd,
        CASE WHEN pc.project_type = 'FPP' THEN fr.revenue_original_amount
            WHEN pc.project_type IN ('T&M', 'AMS (On Demand)')
                THEN CASE
                    WHEN ta.revenue_tarifa_base > 0 THEN ta.revenue_tarifa_base
                    ELSE COALESCE(bi.revenue_base_amount, 0)
                END
            WHEN pc.project_type IN (
                    'Iguala',
                    'AMS Baseline', 'AMS (Base Line)',
                    'BPO',
                    'CFDI - Timbrado', 'CFDI-Timbrado'
                )
                THEN COALESCE(bi.revenue_base_amount, 0)
            ELSE CASE
                    WHEN ta.revenue_tarifa_base > 0 THEN ta.revenue_tarifa_base
                    ELSE COALESCE(bi.revenue_base_amount, 0)
                END
        END                                                                 AS revenue_base_amount,
        COALESCE(fac.facturacion_mes_usd, 0)                               AS facturacion_mes_usd,
        ta.costo_directo,
        COALESCE(hp.costo_hundido, 0)                                      AS costo_hundido,
        ta.costo_directo + COALESCE(hp.costo_hundido, 0)                   AS costo_total,
        ta.horas_facturables,
        ta.horas_totales,
        fr.original_contract_amount,
        fr.original_currency                                               AS contract_original_currency,
        fac.original_billing_amount,
        fac.original_billing_amount_usd,
        fac.original_billing_amount_mxn,
        fac.original_currency                                              AS billing_original_currency,
        COALESCE(fac.original_currency, fr.original_currency)              AS original_currency,
        COALESCE(fac.fx_source, fr.fx_source)                              AS fx_source,
        COALESCE(fac.fx_observed_at, fr.fx_observed_at)                    AS fx_observed_at,
        CASE WHEN pc.project_type = 'FPP'
                 AND COALESCE(fr.contract_financial_status, 'insufficient_data') <> 'ready'
                THEN COALESCE(fr.contract_financial_status, 'insufficient_data')
            WHEN COALESCE(fac.billing_financial_status, 'insufficient_data') <> 'ready'
                THEN COALESCE(fac.billing_financial_status, 'insufficient_data')
            ELSE 'missing_base_currency'
        END                                                                AS financial_status
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
    CASE WHEN financial_status = 'ready' THEN ROUND(revenue_usd, 2) END    AS revenue_usd,
    CASE WHEN financial_status = 'ready'
         THEN ROUND(facturacion_mes_usd, 2) END                            AS facturacion_mes_usd,
    CASE WHEN financial_status = 'ready'
         THEN ROUND(revenue_usd - facturacion_mes_usd, 2) END              AS wip_usd,
    CASE WHEN financial_status = 'ready' THEN ROUND(costo_directo, 2) END  AS costo_directo,
    CASE WHEN financial_status = 'ready' THEN ROUND(costo_hundido, 2) END  AS costo_hundido,
    CASE WHEN financial_status = 'ready' THEN ROUND(costo_total, 2) END    AS costo_total,
    CASE WHEN financial_status = 'ready'
         THEN ROUND(revenue_usd - costo_total, 2) END                      AS margen_bruto_usd,
    ROUND(
        CASE WHEN financial_status = 'ready' AND revenue_usd > 0
             THEN (revenue_usd - costo_total) / revenue_usd * 100
             ELSE NULL END, 2)                                             AS margen_bruto_pct,
    ROUND(horas_facturables, 2)                                            AS horas_facturables,
    ROUND(horas_totales, 2)                                                AS horas_totales,
    ROUND(pct_avance_real, 2)                                              AS pct_avance_real,
    original_contract_amount,
    contract_original_currency,
    original_billing_amount,
    original_billing_amount_usd,
    original_billing_amount_mxn,
    revenue_base_amount,
    costo_directo                                                         AS cost_direct_base_amount,
    costo_hundido                                                         AS cost_sunk_base_amount,
    NULL::VARCHAR                                                         AS base_currency,
    billing_original_currency,
    original_currency,
    fx_source,
    fx_observed_at,
    financial_status
FROM pnl_base
ORDER BY mes DESC, revenue_manager, cliente, proyecto
