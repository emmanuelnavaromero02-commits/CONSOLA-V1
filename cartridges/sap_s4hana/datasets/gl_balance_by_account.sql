-- gl_balance_by_account  (gold)  cartridge: sap_s4hana
-- sources: ["raw/sap_s4hana/JournalEntryItem"]
-- description: Saldo contable por sociedad, cuenta de mayor y ejercicio fiscal, agregado desde las partidas del Universal Journal (ACDOCA).

WITH je AS (
    SELECT
        CompanyCode                              AS company_code,
        GLAccount                                AS gl_account,
        FiscalYear                               AS fiscal_year,
        CAST(AmountInCompanyCodeCurrency AS DECIMAL(17,2)) AS amount
    FROM read_parquet('s3://{bucket}/raw/sap_s4hana/JournalEntryItem/**/*.parquet',
                      hive_partitioning = true,
                      union_by_name   = true)
    WHERE load_date = (SELECT MAX(load_date)
                       FROM read_parquet('s3://{bucket}/raw/sap_s4hana/JournalEntryItem/**/*.parquet',
                                          hive_partitioning = true))
)
SELECT
    company_code              AS company_code,
    gl_account                AS gl_account,
    fiscal_year               AS fiscal_year,
    ROUND(SUM(amount), 2)     AS balance,
    COUNT(*)                  AS line_items
FROM je
GROUP BY company_code, gl_account, fiscal_year
ORDER BY company_code, gl_account, fiscal_year
