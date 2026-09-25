-- sap_b1_journal_lines  (silver)  cartridge: sap_b1
-- sources: ["raw/sap_b1/OJDT", "raw/sap_b1/JDT1", "raw/sap_b1/OACT", "raw/sap_b1/OADM", "raw/sap_b1/IntercompanyPartners"]
-- description: Journal entry lines with their entry header, the account's type (N balance sheet, I income, E expense), the partner code that control-account lines carry in ShortName, and amounts in local, foreign and system currency.

WITH entries AS (
    SELECT * EXCLUDE (_rn)
    FROM (
        SELECT *,
               ROW_NUMBER() OVER (
                   PARTITION BY _company, TransId
                   ORDER BY _source_updated_at DESC NULLS LAST, load_date DESC, _extracted_at DESC
               ) AS _rn
        FROM read_parquet('s3://{bucket}/raw/sap_b1/OJDT/**/*.parquet',
                          hive_partitioning = true,
                          union_by_name   = true)
    )
    WHERE _rn = 1
),
line_versions AS (
    SELECT * EXCLUDE (_rn)
    FROM (
        SELECT *,
               ROW_NUMBER() OVER (
                   PARTITION BY _company, TransId, Line_ID
                   ORDER BY _source_updated_at DESC NULLS LAST, load_date DESC, _extracted_at DESC
               ) AS _rn
        FROM read_parquet('s3://{bucket}/raw/sap_b1/JDT1/**/*.parquet',
                          hive_partitioning = true,
                          union_by_name   = true)
    )
    WHERE _rn = 1
),
lines AS (
    SELECT * EXCLUDE (_entry_stamp)
    FROM (
        SELECT *, MAX(_source_updated_at) OVER (PARTITION BY _company, TransId) AS _entry_stamp
        FROM line_versions
    )
    WHERE _source_updated_at IS NULL OR _source_updated_at = _entry_stamp
),
accounts AS (
    SELECT * EXCLUDE (_rn)
    FROM (
        SELECT s.*, ROW_NUMBER() OVER (PARTITION BY s._company, s.AcctCode ORDER BY s._extracted_at DESC) AS _rn
        FROM read_parquet('s3://{bucket}/raw/sap_b1/OACT/**/*.parquet',
                          hive_partitioning = true,
                          union_by_name   = true) s
        JOIN (
            SELECT _company, arg_max(regexp_replace(_run_id, '-b[0-9]+$', ''), _extracted_at) AS _run_key
            FROM read_parquet('s3://{bucket}/raw/sap_b1/OACT/**/*.parquet',
                          hive_partitioning = true,
                          union_by_name   = true)
            GROUP BY _company
        ) n ON n._company = s._company AND regexp_replace(s._run_id, '-b[0-9]+$', '') = n._run_key
    )
    WHERE _rn = 1
),
company AS (
    SELECT _company, MainCurncy AS local_currency, SysCurrncy AS sys_currency
    FROM (
        SELECT s.*, ROW_NUMBER() OVER (PARTITION BY s._company, s.Code ORDER BY s._extracted_at DESC) AS _rn
        FROM read_parquet('s3://{bucket}/raw/sap_b1/OADM/**/*.parquet',
                          hive_partitioning = true,
                          union_by_name   = true) s
        JOIN (
            SELECT _company, arg_max(regexp_replace(_run_id, '-b[0-9]+$', ''), _extracted_at) AS _run_key
            FROM read_parquet('s3://{bucket}/raw/sap_b1/OADM/**/*.parquet',
                          hive_partitioning = true,
                          union_by_name   = true)
            GROUP BY _company
        ) n ON n._company = s._company AND regexp_replace(s._run_id, '-b[0-9]+$', '') = n._run_key
    )
    WHERE _rn = 1
),
partners AS (
    SELECT _company, CardCode, CounterpartyCompany
    FROM (
        SELECT s.*, ROW_NUMBER() OVER (PARTITION BY s._company, s.CardCode ORDER BY s._extracted_at DESC) AS _rn
        FROM read_parquet('s3://{bucket}/raw/sap_b1/IntercompanyPartners/**/*.parquet',
                          hive_partitioning = true,
                          union_by_name   = true) s
        JOIN (
            SELECT _company, arg_max(regexp_replace(_run_id, '-b[0-9]+$', ''), _extracted_at) AS _run_key
            FROM read_parquet('s3://{bucket}/raw/sap_b1/IntercompanyPartners/**/*.parquet',
                          hive_partitioning = true,
                          union_by_name   = true)
            GROUP BY _company
        ) n ON n._company = s._company AND regexp_replace(s._run_id, '-b[0-9]+$', '') = n._run_key
    )
    WHERE _rn = 1
)
SELECT
    e._company                                              AS company,
    CAST(e.TransId AS BIGINT)                               AS trans_id,
    CAST(l.Line_ID AS BIGINT)                               AS line_id,
    CAST(e.Number AS BIGINT)                                AS entry_number,
    CAST(e.RefDate AS TIMESTAMP)                            AS ref_date,
    CAST(DATE_TRUNC('month', CAST(e.RefDate AS TIMESTAMP)) AS DATE) AS doc_month,
    CAST(e.DueDate AS TIMESTAMP)                            AS due_date,
    CAST(e.TaxDate AS TIMESTAMP)                            AS tax_date,
    CAST(e.TransType AS VARCHAR)                            AS origin_obj_type,
    CAST(e.BaseRef AS VARCHAR)                              AS base_ref,
    CAST(e.StornoToTr AS BIGINT)                            AS reverses_trans_id,
    CAST(e.Memo AS VARCHAR)                                 AS memo,
    CAST(l.Account AS VARCHAR)                              AS account,
    CAST(a.AcctName AS VARCHAR)                             AS account_name,
    CAST(a.ActType AS VARCHAR)                              AS account_type,
    CAST(a.Postable AS VARCHAR) = 'Y'                       AS postable,
    CAST(l.ShortName AS VARCHAR)                            AS short_name,
    CASE WHEN CAST(l.ShortName AS VARCHAR) <> CAST(l.Account AS VARCHAR)
         THEN CAST(l.ShortName AS VARCHAR) END              AS partner_code,
    p.CardCode IS NOT NULL                                  AS is_intercompany_partner,
    CAST(p.CounterpartyCompany AS VARCHAR)                  AS counterparty_company,
    CAST(l.ContraAct AS VARCHAR)                            AS contra_account,
    CAST(l.ProfitCode AS VARCHAR)                           AS cost_centre,
    c.local_currency                                        AS local_currency,
    c.sys_currency                                          AS sys_currency,
    CAST(l.Debit AS DECIMAL(19,6))                          AS debit_local,
    CAST(l.Credit AS DECIMAL(19,6))                         AS credit_local,
    CAST(l.Debit - l.Credit AS DECIMAL(19,6))               AS net_debit_local,
    CAST(l.FCCurrency AS VARCHAR)                           AS fc_currency,
    CAST(l.FCDebit AS DECIMAL(19,6))                        AS debit_fc,
    CAST(l.FCCredit AS DECIMAL(19,6))                       AS credit_fc,
    CAST(l.SYSDeb AS DECIMAL(19,6))                         AS debit_sys,
    CAST(l.SYSCred AS DECIMAL(19,6))                        AS credit_sys,
    CAST(l.SYSDeb - l.SYSCred AS DECIMAL(19,6))             AS net_debit_sys,
    CAST(l.ObjType AS VARCHAR)                              AS line_obj_type,
    CAST(l.LineMemo AS VARCHAR)                             AS line_memo,
    e._source_updated_at                                    AS source_updated_at,
    e.load_date
FROM lines l
JOIN entries e ON e._company = l._company AND e.TransId = l.TransId
LEFT JOIN accounts a ON a._company = l._company AND a.AcctCode = l.Account
LEFT JOIN company c ON c._company = e._company
LEFT JOIN partners p ON p._company = l._company AND p.CardCode = l.ShortName
ORDER BY company, trans_id, line_id
