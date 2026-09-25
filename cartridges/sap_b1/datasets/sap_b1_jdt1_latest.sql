-- sap_b1_jdt1_latest  (silver)  cartridge: sap_b1
-- sources: ["raw/sap_b1/JDT1"]
-- description: Journal lines in local, foreign and system currency; ShortName carries the CardCode on control-account lines.

WITH versions AS (
    SELECT *
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
current_lines AS (
    SELECT *
    FROM (
        SELECT *,
               MAX(_source_updated_at) OVER (PARTITION BY _company, TransId) AS _header_stamp
        FROM versions
    )
    WHERE _source_updated_at IS NULL OR _source_updated_at = _header_stamp
)
SELECT
    CAST(TransId AS BIGINT)                     AS trans_id,
    CAST(Line_ID AS BIGINT)                     AS line_id,
    CAST(Account AS VARCHAR)                    AS account,
    CAST(ShortName AS VARCHAR)                  AS short_name,
    CAST(ContraAct AS VARCHAR)                  AS contra_act,
    CAST(Debit AS DECIMAL(19,6))                AS debit,
    CAST(Credit AS DECIMAL(19,6))               AS credit,
    CAST(FCDebit AS DECIMAL(19,6))              AS fc_debit,
    CAST(FCCredit AS DECIMAL(19,6))             AS fc_credit,
    CAST(FCCurrency AS VARCHAR)                 AS fc_currency,
    CAST(SYSDeb AS DECIMAL(19,6))               AS sys_deb,
    CAST(SYSCred AS DECIMAL(19,6))              AS sys_cred,
    CAST(ProfitCode AS VARCHAR)                 AS profit_code,
    CAST(RefDate AS TIMESTAMP)                  AS ref_date,
    CAST(DueDate AS TIMESTAMP)                  AS due_date,
    CAST(TaxDate AS TIMESTAMP)                  AS tax_date,
    CAST(BaseRef AS VARCHAR)                    AS base_ref,
    CAST(TransType AS VARCHAR)                  AS trans_type,
    CAST(ObjType AS VARCHAR)                    AS obj_type,
    CAST(LineMemo AS VARCHAR)                   AS line_memo,
    _company                              AS company,
    _source_updated_at                    AS source_updated_at,
    load_date
FROM current_lines
ORDER BY company, trans_id, line_id
