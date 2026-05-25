-- sap_s4hana_glaccount_latest  (silver)  cartridge: sap_s4hana
-- sources: ["raw/sap_s4hana/GLAccount"]
-- description: Última extracción del plan de cuentas (A_GLAccountInChartOfAccounts).

-- NOTA: nombres de campo estándar del chart of accounts; ajustar al tenant si difiere.
WITH latest AS (
    SELECT *
    FROM read_parquet('s3://{bucket}/raw/sap_s4hana/GLAccount/**/*.parquet',
                      hive_partitioning = true,
                      union_by_name   = true)
    WHERE load_date = (SELECT MAX(load_date)
                       FROM read_parquet('s3://{bucket}/raw/sap_s4hana/GLAccount/**/*.parquet',
                                          hive_partitioning = true))
)
SELECT
    GLAccount            AS gl_account,
    GLAccountType        AS gl_account_type,
    GLAccountGroup       AS gl_account_group,
    load_date
FROM latest
ORDER BY gl_account
