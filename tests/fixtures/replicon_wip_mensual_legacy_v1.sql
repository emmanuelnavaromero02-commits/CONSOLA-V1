-- -------------------------------------------------------
-- FX: tasa promedio mensual MXN→USD
-- Frankfurter API via excel_billing cartridge.
-- avg_rate = USD por 1 MXN (e.g. 0.0513)
-- -------------------------------------------------------
WITH fx AS (
    SELECT
        DATE_TRUNC('month', TRY_CAST(year_month AS DATE)) AS month,
        avg_rate AS mxn_to_usd
    FROM read_parquet('s3://{bucket}/raw/fx_rates/mxn_usd/fx_rates.parquet')
),
-- -------------------------------------------------------
-- Rate efectivo en USD por hora, por proyecto/mes.
-- Replicon ya convierte en billableamountbasecurrency (USD).
-- rate_usd = billableamountbasecurrency / billabledurationhours
-- -------------------------------------------------------
project_rate_monthly AS (
    SELECT
        projectid,
        DATE_TRUNC('month', TRY_CAST(entrydate AS DATE))            AS month,
        SUM(billableamountbasecurrency) /
            NULLIF(SUM(billabledurationhours), 0)                    AS rate_usd
    FROM read_parquet('s3://{bucket}/raw/replicon/BillingItem/load_date=*/batch_id=*/*.parquet')
    WHERE billabledurationhours > 0
    GROUP BY 1, 2
),
project_rate_default AS (
    SELECT
        projectid,
        SUM(billableamountbasecurrency) /
            NULLIF(SUM(billabledurationhours), 0)                    AS rate_usd
    FROM read_parquet('s3://{bucket}/raw/replicon/BillingItem/load_date=*/batch_id=*/*.parquet')
    WHERE billabledurationhours > 0
    GROUP BY 1
),
-- -------------------------------------------------------
-- Horas trabajadas por mes / proyecto / usuario (TimeEntry)
-- -------------------------------------------------------
time_worked AS (
    SELECT
        DATE_TRUNC('month', TRY_CAST(entrydate AS DATE))             AS month,
        projectid, projectname, clientname, userid, username,
        SUM(durationhours)                                            AS worked_hours,
        SUM(CASE WHEN isbillable THEN durationhours ELSE 0 END)      AS billable_hours
    FROM read_parquet('s3://{bucket}/raw/replicon/TimeEntry/load_date=*/batch_id=*/*.parquet')
    WHERE entrydate IS NOT NULL
    GROUP BY 1, 2, 3, 4, 5, 6
),
-- -------------------------------------------------------
-- Monto facturable trabajado en USD por usuario/proyecto/mes
-- billable_hours × rate_usd (del mes, o histórico del proyecto)
-- -------------------------------------------------------
billable AS (
    SELECT
        t.month, t.projectid, t.projectname, t.clientname,
        t.userid, t.username,
        t.worked_hours,
        t.billable_hours,
        COALESCE(rm.rate_usd, rd.rate_usd, 0)                       AS rate_usd,
        ROUND(t.billable_hours *
              COALESCE(rm.rate_usd, rd.rate_usd, 0), 2)             AS billable_amount_usd
    FROM time_worked t
    LEFT JOIN project_rate_monthly rm
           ON t.projectid = rm.projectid AND t.month = rm.month
    LEFT JOIN project_rate_default rd ON t.projectid = rd.projectid
),
-- -------------------------------------------------------
-- Lo facturado al cliente (Excel externo, excluye Canceladas).
-- MXN convertido a USD con tasa promedio del mes.
-- Nivel proyecto/mes (el Excel no tiene desglose por usuario).
-- -------------------------------------------------------
billed AS (
    SELECT
        DATE_TRUNC('month', TRY_CAST(invoice_date AS DATE))          AS month,
        project_id                                                    AS projectid,
        SUM(CASE
            WHEN currency = 'USD' THEN billed_amount
            WHEN currency = 'MXN' THEN
                billed_amount * COALESCE(fx.mxn_to_usd, 0.05)
            ELSE billed_amount
        END)                                                          AS billed_amount_usd
    FROM read_parquet('s3://{bucket}/raw/excel_billing/invoices/load_date=*/*.parquet') inv
    LEFT JOIN fx
           ON DATE_TRUNC('month', TRY_CAST(inv.invoice_date AS DATE)) = fx.month
    WHERE invoice_date IS NOT NULL
      AND invoice_status != 'Cancelada'
      AND project_id IS NOT NULL AND project_id != '0'
    GROUP BY 1, 2
),
-- -------------------------------------------------------
-- Prorratear billed por usuario según su % de horas billables
-- (el Excel no tiene desglose por usuario)
-- -------------------------------------------------------
project_billable_totals AS (
    SELECT month, projectid, SUM(billable_hours) AS total_billable
    FROM billable GROUP BY 1, 2
),
billed_per_user AS (
    SELECT
        b.month, b.projectid, bl.userid,
        ROUND(b.billed_amount_usd *
              bl.billable_hours / NULLIF(pt.total_billable, 0), 2)   AS user_billed_usd
    FROM billed b
    JOIN billable bl    ON b.month = bl.month AND b.projectid = bl.projectid
    JOIN project_billable_totals pt
                        ON b.month = pt.month AND b.projectid = pt.projectid
)
SELECT
    bl.month,
    bl.projectid,
    bl.projectname,
    bl.clientname,
    bl.userid,
    bl.username,
    bl.worked_hours,
    bl.billable_hours,
    bl.rate_usd                                                       AS billing_rate_usd,
    bl.billable_amount_usd,
    COALESCE(bu.user_billed_usd, 0)                                  AS billed_amount_usd,
    ROUND(bl.billable_amount_usd -
          COALESCE(bu.user_billed_usd, 0), 2)                        AS wip_amount_usd,
    ROUND(COALESCE(bu.user_billed_usd, 0) /
          NULLIF(bl.billable_amount_usd, 0) * 100, 1)                AS billing_completion_pct
FROM billable bl
LEFT JOIN billed_per_user bu
       ON bl.month = bu.month AND bl.projectid = bu.projectid AND bl.userid = bu.userid
WHERE bl.billable_hours > 0
ORDER BY bl.month DESC, wip_amount_usd DESC
