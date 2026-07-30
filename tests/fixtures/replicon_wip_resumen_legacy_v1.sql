WITH fx AS (
    SELECT
        DATE_TRUNC('month', TRY_CAST(year_month AS DATE)) AS month,
        avg_rate AS mxn_to_usd
    FROM read_parquet('s3://{bucket}/raw/fx_rates/mxn_usd/fx_rates.parquet')
),
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
time_worked AS (
    SELECT
        DATE_TRUNC('month', TRY_CAST(entrydate AS DATE))            AS month,
        projectid, projectname, clientname,
        SUM(durationhours)                                           AS worked_hours,
        SUM(CASE WHEN isbillable THEN durationhours ELSE 0 END)     AS billable_hours,
        COUNT(DISTINCT userid)                                       AS contributors
    FROM read_parquet('s3://{bucket}/raw/replicon/TimeEntry/load_date=*/batch_id=*/*.parquet')
    WHERE entrydate IS NOT NULL
    GROUP BY 1, 2, 3, 4
),
billable AS (
    SELECT
        t.month, t.projectid, t.projectname, t.clientname,
        t.worked_hours, t.billable_hours, t.contributors,
        COALESCE(rm.rate_usd, rd.rate_usd, 0)                      AS rate_usd,
        ROUND(t.billable_hours *
              COALESCE(rm.rate_usd, rd.rate_usd, 0), 2)            AS billable_amount_usd
    FROM time_worked t
    LEFT JOIN project_rate_monthly rm
           ON t.projectid = rm.projectid AND t.month = rm.month
    LEFT JOIN project_rate_default rd ON t.projectid = rd.projectid
),
billed AS (
    SELECT
        DATE_TRUNC('month', TRY_CAST(invoice_date AS DATE))         AS month,
        project_id                                                   AS projectid,
        SUM(CASE
            WHEN currency = 'USD' THEN billed_amount
            WHEN currency = 'MXN' THEN
                billed_amount * COALESCE(fx.mxn_to_usd, 0.05)
            ELSE billed_amount
        END)                                                         AS billed_amount_usd
    FROM read_parquet('s3://{bucket}/raw/excel_billing/invoices/load_date=*/*.parquet') inv
    LEFT JOIN fx
           ON DATE_TRUNC('month', TRY_CAST(inv.invoice_date AS DATE)) = fx.month
    WHERE invoice_date IS NOT NULL
      AND invoice_status != 'Cancelada'
      AND project_id IS NOT NULL AND project_id != '0'
    GROUP BY 1, 2
)
SELECT
    bl.month,
    bl.projectid,
    bl.projectname,
    bl.clientname,
    bl.contributors,
    bl.worked_hours,
    bl.billable_hours,
    bl.rate_usd                                                      AS billing_rate_usd,
    bl.billable_amount_usd,
    COALESCE(b.billed_amount_usd, 0)                                AS billed_amount_usd,
    ROUND(bl.billable_amount_usd -
          COALESCE(b.billed_amount_usd, 0), 2)                      AS wip_amount_usd,
    ROUND(COALESCE(b.billed_amount_usd, 0) /
          NULLIF(bl.billable_amount_usd, 0) * 100, 1)               AS billing_completion_pct,
    CASE
        WHEN bl.billable_amount_usd = 0                             THEN 'sin_monto'
        WHEN COALESCE(b.billed_amount_usd, 0) /
             NULLIF(bl.billable_amount_usd, 0) >= 0.9               THEN 'facturado'
        WHEN COALESCE(b.billed_amount_usd, 0) /
             NULLIF(bl.billable_amount_usd, 0) >= 0.5               THEN 'parcial'
        ELSE                                                              'pendiente'
    END                                                              AS wip_status
FROM billable bl
LEFT JOIN billed b ON bl.month = b.month AND bl.projectid = b.projectid
WHERE bl.billable_hours > 0
ORDER BY bl.month DESC, wip_amount_usd DESC
