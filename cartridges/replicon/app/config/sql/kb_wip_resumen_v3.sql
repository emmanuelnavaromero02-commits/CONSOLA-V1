WITH detail AS (
    SELECT * FROM (
        __WIP_MENSUAL_V3__
    ) monthly
)
SELECT month, projectid, MAX(projectname) AS projectname,
       MAX(clientname) AS clientname, COUNT(DISTINCT userid) AS contributors,
       SUM(worked_hours) AS worked_hours, SUM(billable_hours) AS billable_hours,
       CASE WHEN COUNT(DISTINCT rate_currency) = 1 THEN MAX(rate_currency) END AS rate_currency,
       CASE WHEN COUNT(DISTINCT rate_currency) = 1 THEN MAX(billing_rate_original) END AS billing_rate_original,
       CASE WHEN BOOL_AND(financial_status = 'ready') THEN MAX(billing_rate_usd) END AS billing_rate_usd,
       CASE WHEN COUNT(DISTINCT rate_currency) = 1 THEN SUM(billable_amount_original) END AS billable_amount_original,
       CASE WHEN BOOL_AND(financial_status = 'ready') THEN SUM(billable_amount_usd) END AS billable_amount_usd,
       SUM(original_billed_usd) AS original_billed_usd,
       SUM(original_billed_mxn) AS original_billed_mxn,
       CASE WHEN COUNT(DISTINCT original_billed_currency) = 1
            THEN MAX(original_billed_currency) END AS original_billed_currency,
       CASE WHEN COUNT(DISTINCT original_billed_currency) = 1
            THEN SUM(original_billed_amount) END AS original_billed_amount,
       CASE WHEN BOOL_AND(billing_data_status = 'ready') THEN 'ready'
            WHEN BOOL_OR(billing_data_status = 'missing_base_currency') THEN 'missing_base_currency'
            WHEN BOOL_OR(billing_data_status = 'missing_fx') THEN 'missing_fx'
            ELSE 'insufficient_data' END AS billing_data_status,
       CASE WHEN BOOL_AND(financial_status = 'ready') THEN 'ready'
            WHEN BOOL_OR(financial_status = 'missing_base_currency') THEN 'missing_base_currency'
            WHEN BOOL_OR(financial_status = 'missing_fx') THEN 'missing_fx'
            ELSE 'insufficient_data' END AS financial_status,
       CASE WHEN BOOL_AND(financial_status = 'ready') THEN SUM(billed_amount_usd) END AS billed_amount_usd,
       CASE WHEN BOOL_AND(financial_status = 'ready') THEN SUM(wip_amount_usd) END AS wip_amount_usd,
       CASE WHEN BOOL_AND(financial_status = 'ready')
            THEN SUM(billed_amount_usd) / NULLIF(SUM(billable_amount_usd), 0) * 100 END AS billing_completion_pct,
       CASE WHEN NOT BOOL_AND(financial_status = 'ready') THEN
                  CASE WHEN BOOL_OR(financial_status = 'missing_base_currency') THEN 'missing_base_currency'
                       WHEN BOOL_OR(financial_status = 'missing_fx') THEN 'missing_fx'
                       ELSE 'insufficient_data' END
            WHEN SUM(billable_amount_usd) = 0 THEN 'sin_monto'
            WHEN SUM(billed_amount_usd) / NULLIF(SUM(billable_amount_usd), 0) >= 0.9 THEN 'facturado'
            WHEN SUM(billed_amount_usd) / NULLIF(SUM(billable_amount_usd), 0) >= 0.5 THEN 'parcial'
            ELSE 'pendiente' END AS wip_status
  FROM detail GROUP BY month, projectid ORDER BY month DESC, wip_amount_usd DESC NULLS LAST;
