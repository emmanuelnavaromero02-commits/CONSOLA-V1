CREATE OR REPLACE MACRO sf_odata_timestamp(raw) AS (
    CASE
        WHEN raw IS NULL OR trim(CAST(raw AS VARCHAR)) = '' THEN NULL
        WHEN regexp_full_match(
            trim(CAST(raw AS VARCHAR)),
            '/Date\((-?[0-9]{1,15})(?:[+-][0-9]{1,4})?\)/'
        )
            THEN epoch_ms(CAST(regexp_extract(
                trim(CAST(raw AS VARCHAR)), '^/Date\((-?[0-9]{1,15})', 1
            ) AS BIGINT))
        WHEN regexp_full_match(trim(CAST(raw AS VARCHAR)), '-?[0-9]{12,15}')
            THEN epoch_ms(CAST(trim(CAST(raw AS VARCHAR)) AS BIGINT))
        ELSE TRY_CAST(trim(CAST(raw AS VARCHAR)) AS TIMESTAMP)
    END
);

CREATE OR REPLACE MACRO sf_odata_date(raw) AS (
    CAST(sf_odata_timestamp(raw) AS DATE)
);

CREATE OR REPLACE MACRO sf_odata_date_strict(raw, label) AS (
    CASE
        WHEN raw IS NOT NULL
            AND trim(CAST(raw AS VARCHAR)) <> ''
            AND sf_odata_timestamp(raw) IS NULL
            THEN CAST(error('fecha con formato no reconocido en ' || label) AS DATE)
        ELSE CAST(sf_odata_timestamp(raw) AS DATE)
    END
);
