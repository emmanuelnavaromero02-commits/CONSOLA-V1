-- sap_successfactors_compensation_full  (silver)  cartridge: sap_successfactors
-- sources: ["raw/sap_successfactors/EmpCompensation", "raw/sap_successfactors/EmpPayCompRecurring", "raw/sap_successfactors/EmpPayCompNonRecurring", "silver/sap_successfactors/sap_successfactors_empcompensation_latest", "silver/sap_successfactors/sap_successfactors_emppaycomprecurring_latest", "silver/sap_successfactors/sap_successfactors_emppaycompnonrecurring_latest"]
-- description: Componentes de compensación por empleado (cabecera + recurrentes + no recurrentes). El importe (paycomp_value) llega encrypted desde bronze: se conserva como caja negra y NO es agregable.

-- Unión por user_id (plano en las tres entidades). paycomp_value es un token
-- cifrado: sirve para trazabilidad fila a fila, no para sumas/medias en gold.
WITH header AS (
    SELECT user_id, pay_group, frequency_code,
           ROW_NUMBER() OVER (PARTITION BY user_id ORDER BY start_date DESC) AS rn
    FROM read_parquet('s3://{bucket}/silver/sap_successfactors/sap_successfactors_empcompensation_latest/**/*.parquet')
),
recurring AS (
    SELECT user_id, pay_component, paycomp_value, currency, 'recurring' AS kind
    FROM read_parquet('s3://{bucket}/silver/sap_successfactors/sap_successfactors_emppaycomprecurring_latest/**/*.parquet')
),
non_recurring AS (
    SELECT user_id, pay_component, paycomp_value, currency, 'non_recurring' AS kind
    FROM read_parquet('s3://{bucket}/silver/sap_successfactors/sap_successfactors_emppaycompnonrecurring_latest/**/*.parquet')
),
components AS (
    SELECT * FROM recurring
    UNION ALL
    SELECT * FROM non_recurring
)
SELECT
    cmp.user_id          AS user_id,            -- plano
    h.pay_group          AS pay_group,
    h.frequency_code     AS frequency_code,
    cmp.kind             AS component_kind,
    cmp.pay_component    AS pay_component,
    cmp.paycomp_value    AS paycomp_value,      -- encrypted (caja negra)
    cmp.currency         AS currency
FROM components cmp
LEFT JOIN header h ON h.user_id = cmp.user_id AND h.rn = 1
ORDER BY cmp.user_id, cmp.kind, cmp.pay_component
