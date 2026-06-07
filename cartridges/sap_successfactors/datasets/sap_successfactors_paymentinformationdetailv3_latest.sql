-- sap_successfactors_paymentinformationdetailv3_latest  (silver)  cartridge: sap_successfactors
-- sources: ["raw/sap_successfactors/PaymentInformationDetailV3"]
-- description: Última extracción deduplicada de PaymentInformationDetailV3. Campos bancarios sensibles llegan masked/encrypted desde bronze.

WITH raw AS (
    SELECT *
    FROM read_parquet('s3://{bucket}/raw/sap_successfactors/PaymentInformationDetailV3/**/*.parquet',
                      hive_partitioning = true,
                      union_by_name   = true)
),
latest AS (
    SELECT *
    FROM (
        SELECT
            raw.*,
            ROW_NUMBER() OVER (
                PARTITION BY externalCode
                ORDER BY
                    TRY_CAST(lastModifiedDateTime AS TIMESTAMP) DESC NULLS LAST,
                    TRY_CAST(_extracted_at AS TIMESTAMP) DESC NULLS LAST,
                    TRY_CAST(load_date AS DATE) DESC NULLS LAST,
                    CAST(batch_id AS VARCHAR) DESC NULLS LAST
            ) AS _rn
        FROM raw
        WHERE externalCode IS NOT NULL
    )
    WHERE _rn = 1
)
SELECT
    externalCode AS payment_detail_id,
    PaymentInformationV3_worker AS worker_id,
    TRY_CAST(PaymentInformationV3_effectiveStartDate AS DATE) AS effective_start_date,
    TRY_CAST(mdfSystemEffectiveStartDate AS DATE) AS system_effective_start_date,
    TRY_CAST(mdfSystemEffectiveEndDate AS DATE) AS system_effective_end_date,
    paymentMethod AS payment_method,
    bankCountry AS bank_country,
    bank AS bank,
    businessIdentifierCode AS business_identifier_code,
    routingNumber AS routing_number,
    accountNumber AS account_number,
    accountOwner AS account_owner,
    iban AS iban,
    currency AS currency,
    TRY_CAST(amount AS DOUBLE) AS amount,
    TRY_CAST(percent AS DOUBLE) AS percent,
    payType AS pay_type,
    customPayType AS custom_pay_type,
    paySequence AS pay_sequence,
    purpose AS purpose,
    load_date
FROM latest
ORDER BY worker_id, payment_detail_id
