-- 99za_sap_successfactors_select_hardening.sql
--
-- Live SuccessFactors hardening from AWS preflight:
-- - Pin explicit OData v2 $select fields for broad entities that previously
--   depended on implicit all-column reads.
-- - Replace tenant-invalid EmpEmployment.employeeClass with assignmentClass.
-- - Align Position primary key with the actual OData entityset field: code.
--
-- Idempotent and cartridge-scoped.

UPDATE entity_config
   SET select_fields = '["personIdExternal","phoneType","phoneNumber","isPrimary","lastModifiedDateTime"]'::jsonb,
       protection = '{"phoneNumber":"masked"}'::jsonb
 WHERE cartridge_id = 'sap_successfactors'
   AND entity = 'PerPhone';

UPDATE entity_config
   SET select_fields = '["personIdExternal","addressType","startDate","endDate","address1","city","state","zipCode","lastModifiedDateTime"]'::jsonb,
       protection = '{"address1":"masked","zipCode":"masked"}'::jsonb
 WHERE cartridge_id = 'sap_successfactors'
   AND entity = 'PerAddressDEFLT';

UPDATE entity_config
   SET select_fields = '["personIdExternal","country","cardType","nationalId","isPrimary","lastModifiedDateTime"]'::jsonb,
       protection = '{"nationalId":"encrypted"}'::jsonb
 WHERE cartridge_id = 'sap_successfactors'
   AND entity = 'PerNationalId';

UPDATE entity_config
   SET effective_dated = FALSE,
       date_field = NULL,
       select_fields = '["personIdExternal","userId","startDate","endDate","assignmentClass","originalStartDate","lastModifiedDateTime"]'::jsonb
 WHERE cartridge_id = 'sap_successfactors'
   AND entity = 'EmpEmployment';

UPDATE entity_config
   SET effective_dated = TRUE,
       date_field = 'startDate',
       select_fields = '["userId","startDate","endDate","payGroup","lastModifiedDateTime"]'::jsonb
 WHERE cartridge_id = 'sap_successfactors'
   AND entity = 'EmpCompensation';

UPDATE entity_config
   SET effective_dated = TRUE,
       date_field = 'startDate',
       select_fields = '["userId","payComponent","paycompvalue","frequency","currencyCode","startDate","endDate","lastModifiedDateTime"]'::jsonb,
       protection = '{"paycompValue":"encrypted","paycompvalue":"encrypted"}'::jsonb
 WHERE cartridge_id = 'sap_successfactors'
   AND entity = 'EmpPayCompRecurring';

UPDATE entity_config
   SET date_field = 'payDate',
       select_fields = '["userId","payComponentCode","value","currencyCode","payDate","lastModifiedDateTime"]'::jsonb,
       protection = '{"paycompValue":"encrypted","value":"encrypted"}'::jsonb
 WHERE cartridge_id = 'sap_successfactors'
   AND entity = 'EmpPayCompNonRecurring';

UPDATE entity_config
   SET date_field = 'endDate',
       select_fields = '["userId","endDate","lastModifiedDateTime"]'::jsonb
 WHERE cartridge_id = 'sap_successfactors'
   AND entity = 'EmpEmploymentTermination';

UPDATE entity_config
   SET select_fields = '["externalCode","name_defaultValue","status","startDate","endDate","lastModifiedDateTime"]'::jsonb
 WHERE cartridge_id = 'sap_successfactors'
   AND entity = 'FOCostCenter';

UPDATE entity_config
   SET primary_key = 'code',
       select_fields = '["code","externalName_defaultValue","department","location","costCenter","lastModifiedDateTime"]'::jsonb
 WHERE cartridge_id = 'sap_successfactors'
   AND entity = 'Position';

UPDATE entity_config
   SET select_fields = '["userId","startDate","endDate","timeType","approvalStatus","lastModifiedDateTime"]'::jsonb
 WHERE cartridge_id = 'sap_successfactors'
   AND entity = 'EmployeeTime';

UPDATE entity_config
   SET select_fields = '["userId","accountType","lastModifiedDateTime"]'::jsonb
 WHERE cartridge_id = 'sap_successfactors'
   AND entity = 'TimeAccount';

UPDATE entity_config
   SET select_fields = '["candidateId","firstName","lastName","lastModifiedDateTime"]'::jsonb,
       protection = '{"candidateId":"shadowed","firstName":"masked","lastName":"masked"}'::jsonb
 WHERE cartridge_id = 'sap_successfactors'
   AND entity = 'Candidate';

UPDATE entity_config
   SET odata_entity = 'EmpJobRelationships',
       select_fields = '["userId","startDate","endDate","relationshipType","relUserId","lastModifiedDateTime"]'::jsonb
 WHERE cartridge_id = 'sap_successfactors'
   AND entity = 'EmpJob_History';

UPDATE datasets
   SET sql_def = $dataset$
-- sap_successfactors_emppaycompnonrecurring_latest  (silver)  cartridge: sap_successfactors
-- sources: ["raw/sap_successfactors/EmpPayCompNonRecurring"]
-- description: Última extracción de pagos no recurrentes (bonos, pagos únicos). paycompValue encrypted desde bronze (caja negra; NO agregable).

WITH raw AS (
    SELECT *
    FROM read_parquet('s3://{bucket}/raw/sap_successfactors/EmpPayCompNonRecurring/**/*.parquet',
                      hive_partitioning = true,
                      union_by_name   = true)
),
latest AS (
    SELECT *
    FROM (
        SELECT
            raw.*,
            ROW_NUMBER() OVER (
                PARTITION BY userId, payComponentCode, payDate
                ORDER BY
                    TRY_CAST(lastModifiedDateTime AS TIMESTAMP) DESC NULLS LAST,
                    TRY_CAST(_extracted_at AS TIMESTAMP) DESC NULLS LAST,
                    TRY_CAST(load_date AS DATE) DESC NULLS LAST,
                    CAST(batch_id AS VARCHAR) DESC NULLS LAST
            ) AS _rn
        FROM raw
        WHERE userId IS NOT NULL
    )
    WHERE _rn = 1
)
SELECT
    userId               AS user_id,            -- plano
    payComponentCode     AS pay_component,
    value                AS paycomp_value,      -- encrypted en bronze (no agregable)
    currencyCode         AS currency,
    CAST(payDate AS DATE) AS pay_date,
    load_date
FROM latest
ORDER BY user_id, pay_date
$dataset$,
       updated_at = NOW()
 WHERE name = 'sap_successfactors_emppaycompnonrecurring_latest';

UPDATE datasets
   SET sql_def = $dataset$
-- sap_successfactors_emppaycomprecurring_latest  (silver)  cartridge: sap_successfactors
-- sources: ["raw/sap_successfactors/EmpPayCompRecurring"]
-- description: Última extracción de pagos recurrentes (salario base, complementos). paycompValue llega encrypted desde bronze (caja negra; NO agregable en SQL).

WITH raw AS (
    SELECT *
    FROM read_parquet('s3://{bucket}/raw/sap_successfactors/EmpPayCompRecurring/**/*.parquet',
                      hive_partitioning = true,
                      union_by_name   = true)
),
latest AS (
    SELECT *
    FROM (
        SELECT
            raw.*,
            ROW_NUMBER() OVER (
                PARTITION BY userId, payComponent, startDate
                ORDER BY
                    TRY_CAST(lastModifiedDateTime AS TIMESTAMP) DESC NULLS LAST,
                    TRY_CAST(_extracted_at AS TIMESTAMP) DESC NULLS LAST,
                    TRY_CAST(load_date AS DATE) DESC NULLS LAST,
                    CAST(batch_id AS VARCHAR) DESC NULLS LAST
            ) AS _rn
        FROM raw
        WHERE userId IS NOT NULL
    )
    WHERE _rn = 1
)
SELECT
    userId               AS user_id,            -- plano
    payComponent         AS pay_component,
    paycompvalue         AS paycomp_value,      -- encrypted en bronze (no agregable)
    frequency            AS frequency,
    currencyCode         AS currency,
    CAST(startDate AS DATE) AS start_date,
    load_date
FROM latest
ORDER BY user_id, pay_component, start_date
$dataset$,
       updated_at = NOW()
 WHERE name = 'sap_successfactors_emppaycomprecurring_latest';

INSERT INTO schema_migrations (filename, applied_at)
VALUES ('99za_sap_successfactors_select_hardening.sql', NOW())
ON CONFLICT (filename) DO NOTHING;
