WITH fx_raw AS (
    SELECT DATE_TRUNC('month', TRY_CAST(year_month AS DATE)) AS month,
           CASE WHEN isfinite(TRY_CAST(avg_rate AS DOUBLE))
                     AND TRY_CAST(avg_rate AS DOUBLE) > 0
                THEN TRY_CAST(avg_rate AS DOUBLE) END AS mxn_to_usd
      FROM read_parquet('s3://{bucket}/raw/fx_rates/mxn_usd/fx_rates.parquet')
), fx AS (
    SELECT month,
           CASE WHEN COUNT(*) = 1 THEN MAX(mxn_to_usd) END AS mxn_to_usd
      FROM fx_raw GROUP BY month
), base_config AS (
    SELECT TRY_CAST(effective_from AS DATE) AS effective_from,
           TRY_CAST(effective_to AS DATE) AS effective_to,
           UPPER(TRIM(CAST(currency AS VARCHAR))) AS currency
      FROM replicon_base_currency
), billing_items AS (
    SELECT DATE_TRUNC('month', TRY_CAST(item.entrydate AS DATE)) AS month,
           item.projectid,
           CASE WHEN isfinite(TRY_CAST(item.billabledurationhours AS DOUBLE))
                THEN TRY_CAST(item.billabledurationhours AS DOUBLE) END AS hours,
           CASE WHEN isfinite(TRY_CAST(item.billabledurationhours AS DOUBLE))
                     AND isfinite(TRY_CAST(item.billableamountbasecurrency AS DOUBLE))
                THEN TRY_CAST(item.billableamountbasecurrency AS DOUBLE) END AS amount_original,
           cfg.currency,
           fx.mxn_to_usd,
           CASE
             WHEN TRY_CAST(item.billabledurationhours AS DOUBLE) IS NULL
                  OR NOT isfinite(TRY_CAST(item.billabledurationhours AS DOUBLE))
                  OR TRY_CAST(item.billableamountbasecurrency AS DOUBLE) IS NULL
                  OR NOT isfinite(TRY_CAST(item.billableamountbasecurrency AS DOUBLE))
               THEN 'insufficient_data'
             WHEN cfg.currency IS NULL THEN 'missing_base_currency'
             WHEN cfg.currency = 'USD' THEN 'ready'
             WHEN cfg.currency = 'MXN' AND fx.mxn_to_usd IS NOT NULL THEN 'ready'
             ELSE 'missing_fx'
           END AS item_status,
           CASE
             WHEN isfinite(TRY_CAST(item.billabledurationhours AS DOUBLE))
                  AND isfinite(TRY_CAST(item.billableamountbasecurrency AS DOUBLE))
                  AND cfg.currency = 'USD'
               THEN TRY_CAST(item.billableamountbasecurrency AS DOUBLE)
             WHEN isfinite(TRY_CAST(item.billabledurationhours AS DOUBLE))
                  AND isfinite(TRY_CAST(item.billableamountbasecurrency AS DOUBLE))
                  AND cfg.currency = 'MXN' AND fx.mxn_to_usd IS NOT NULL
               THEN TRY_CAST(item.billableamountbasecurrency AS DOUBLE) * fx.mxn_to_usd
           END AS amount_usd
      FROM read_parquet('s3://{bucket}/raw/replicon/BillingItem/load_date=*/batch_id=*/*.parquet') item
      LEFT JOIN LATERAL (
          SELECT CASE WHEN COUNT(*) = 1 THEN MAX(currency) END AS currency
            FROM base_config
           WHERE TRY_CAST(item.entrydate AS DATE) >= effective_from
             AND (effective_to IS NULL OR TRY_CAST(item.entrydate AS DATE) < effective_to)
      ) cfg ON TRUE
      LEFT JOIN fx ON DATE_TRUNC('month', TRY_CAST(item.entrydate AS DATE)) = fx.month
     WHERE TRY_CAST(item.billabledurationhours AS DOUBLE) IS NULL
        OR NOT isfinite(TRY_CAST(item.billabledurationhours AS DOUBLE))
        OR TRY_CAST(item.billabledurationhours AS DOUBLE) > 0
), project_rate AS (
    SELECT month, projectid,
           CASE WHEN COUNT(DISTINCT currency) = 1 THEN MAX(currency) END AS rate_currency,
           SUM(amount_original) / NULLIF(SUM(hours), 0) AS rate_original,
           SUM(amount_usd) / NULLIF(SUM(hours), 0) AS rate_usd,
           CASE
             WHEN COUNT(*) FILTER (WHERE item_status = 'missing_base_currency') > 0
                  OR COUNT(DISTINCT currency) != 1 THEN 'missing_base_currency'
             WHEN COUNT(*) FILTER (WHERE item_status = 'missing_fx') > 0 THEN 'missing_fx'
             WHEN COUNT(*) FILTER (WHERE item_status = 'insufficient_data') > 0
               THEN 'insufficient_data'
             ELSE 'ready'
           END AS rate_status
      FROM billing_items GROUP BY 1, 2
), time_rows AS (
    SELECT DATE_TRUNC('month', TRY_CAST(entrydate AS DATE)) AS month,
           projectid, projectname, clientname, userid, username,
           CASE WHEN isfinite(TRY_CAST(durationhours AS DOUBLE))
                THEN TRY_CAST(durationhours AS DOUBLE) END AS duration_hours,
           isbillable,
           CASE WHEN isfinite(TRY_CAST(durationhours AS DOUBLE))
                     AND isbillable IS NOT NULL
                THEN 'ready' ELSE 'insufficient_data' END AS time_row_status
      FROM read_parquet('s3://{bucket}/raw/replicon/TimeEntry/load_date=*/batch_id=*/*.parquet')
     WHERE entrydate IS NOT NULL
), time_worked AS (
    SELECT month, projectid, projectname, clientname, userid, username,
           SUM(duration_hours) AS worked_hours,
           SUM(CASE WHEN isbillable THEN duration_hours ELSE 0 END) AS billable_hours,
           CASE WHEN COUNT(*) FILTER (WHERE time_row_status != 'ready') > 0
                THEN 'insufficient_data' ELSE 'ready' END AS time_data_status
      FROM time_rows GROUP BY 1, 2, 3, 4, 5, 6
), billable AS (
    SELECT time_worked.*, project_rate.rate_currency,
           CASE WHEN time_worked.time_data_status = 'ready'
                     AND project_rate.rate_status IN ('ready', 'missing_fx')
                THEN project_rate.rate_original END AS billing_rate_original,
           CASE WHEN time_worked.time_data_status = 'ready'
                     AND project_rate.rate_status = 'ready'
                THEN project_rate.rate_usd END AS billing_rate_usd,
           CASE WHEN time_worked.time_data_status = 'ready'
                     AND project_rate.rate_status IN ('ready', 'missing_fx')
                THEN time_worked.billable_hours * project_rate.rate_original END AS billable_amount_original,
           CASE WHEN time_worked.time_data_status = 'ready'
                     AND project_rate.rate_status = 'ready'
                THEN time_worked.billable_hours * project_rate.rate_usd END AS billable_amount_usd,
           CASE WHEN time_worked.time_data_status != 'ready'
                THEN time_worked.time_data_status
                ELSE COALESCE(project_rate.rate_status, 'missing_base_currency') END AS rate_status
      FROM time_worked LEFT JOIN project_rate USING (month, projectid)
), invoice_rows AS (
    SELECT DATE_TRUNC('month', TRY_CAST(invoice_date AS DATE)) AS month,
           project_id AS projectid, UPPER(TRIM(CAST(currency AS VARCHAR))) AS currency,
           CASE WHEN isfinite(TRY_CAST(billed_amount AS DOUBLE))
                THEN TRY_CAST(billed_amount AS DOUBLE) END AS billed_amount,
           fx.mxn_to_usd
      FROM read_parquet('s3://{bucket}/raw/excel_billing/invoices/load_date=*/*.parquet') inv
      LEFT JOIN fx ON DATE_TRUNC('month', TRY_CAST(inv.invoice_date AS DATE)) = fx.month
     WHERE invoice_date IS NOT NULL AND invoice_status != 'Cancelada'
       AND project_id IS NOT NULL AND project_id != '0'
), billed AS (
    SELECT month, projectid,
           SUM(CASE WHEN currency = 'USD' THEN billed_amount END) AS original_billed_usd,
           SUM(CASE WHEN currency = 'MXN' THEN billed_amount END) AS original_billed_mxn,
           CASE WHEN COUNT(*) = COUNT(currency) AND COUNT(DISTINCT currency) = 1
                THEN MAX(currency) END AS original_billed_currency,
           CASE WHEN COUNT(*) = COUNT(currency) AND COUNT(DISTINCT currency) = 1
                THEN SUM(billed_amount) END AS original_billed_amount,
           CASE
             WHEN COUNT(*) FILTER (WHERE currency IS NULL OR currency = 'UNKNOWN') > 0
               THEN 'missing_base_currency'
             WHEN COUNT(*) FILTER (WHERE currency NOT IN ('USD', 'MXN')) > 0
                  OR COUNT(*) FILTER (WHERE currency = 'MXN' AND mxn_to_usd IS NULL) > 0
               THEN 'missing_fx'
             WHEN COUNT(*) FILTER (WHERE billed_amount IS NULL) > 0 THEN 'insufficient_data'
             ELSE 'ready'
           END AS billing_data_status,
           SUM(CASE WHEN currency = 'USD' THEN billed_amount
                    WHEN currency = 'MXN' AND mxn_to_usd IS NOT NULL
                      THEN billed_amount * mxn_to_usd END) AS converted_billed_amount_usd
      FROM invoice_rows GROUP BY 1, 2
), project_billable AS (
    SELECT month, projectid, SUM(billable_hours) AS total_billable
      FROM billable GROUP BY 1, 2
), billed_per_user AS (
    SELECT billed.month, billed.projectid, billable.userid,
           billed.original_billed_usd * billable.billable_hours / NULLIF(project_billable.total_billable, 0) AS original_billed_usd,
           billed.original_billed_mxn * billable.billable_hours / NULLIF(project_billable.total_billable, 0) AS original_billed_mxn,
           billed.original_billed_currency,
           billed.original_billed_amount * billable.billable_hours / NULLIF(project_billable.total_billable, 0) AS original_billed_amount,
           billed.billing_data_status,
           billed.converted_billed_amount_usd * billable.billable_hours / NULLIF(project_billable.total_billable, 0) AS converted_billed_amount_usd
      FROM billed JOIN billable USING (month, projectid)
      JOIN project_billable USING (month, projectid)
), combined AS (
    SELECT billable.*, billed_per_user.original_billed_usd,
           billed_per_user.original_billed_mxn,
           billed_per_user.original_billed_currency,
           billed_per_user.original_billed_amount,
           COALESCE(billed_per_user.billing_data_status, 'insufficient_data') AS billing_data_status,
           billed_per_user.converted_billed_amount_usd,
           CASE
             WHEN billable.rate_status != 'ready' THEN billable.rate_status
             WHEN billed_per_user.billing_data_status != 'ready' THEN billed_per_user.billing_data_status
             WHEN billed_per_user.billing_data_status IS NULL THEN 'insufficient_data'
             ELSE 'ready'
           END AS financial_status
      FROM billable LEFT JOIN billed_per_user USING (month, projectid, userid)
     WHERE billable.billable_hours > 0
)
SELECT month, projectid, projectname, clientname, userid, username,
       worked_hours, billable_hours, rate_currency, billing_rate_original,
       billing_rate_usd, billable_amount_original,
       CASE WHEN financial_status = 'ready' THEN billable_amount_usd END AS billable_amount_usd,
       original_billed_usd, original_billed_mxn, original_billed_currency,
       original_billed_amount, billing_data_status, financial_status,
       CASE WHEN financial_status = 'ready' THEN converted_billed_amount_usd END AS billed_amount_usd,
       CASE WHEN financial_status = 'ready' THEN billable_amount_usd - converted_billed_amount_usd END AS wip_amount_usd,
       CASE WHEN financial_status = 'ready' THEN converted_billed_amount_usd / NULLIF(billable_amount_usd, 0) * 100 END AS billing_completion_pct
  FROM combined ORDER BY month DESC, wip_amount_usd DESC NULLS LAST;
