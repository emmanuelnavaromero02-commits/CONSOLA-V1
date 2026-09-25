-- sap_b1_ospp_latest  (silver)  cartridge: sap_b1
-- sources: ["raw/sap_b1/OSPP"]
-- description: Special prices per business partner and item (price, currency, discount, validity).

WITH newest_run AS (
    SELECT _company,
           arg_max(regexp_replace(_run_id, '-b[0-9]+$', ''), _extracted_at) AS _run_key
    FROM read_parquet('s3://{bucket}/raw/sap_b1/OSPP/**/*.parquet',
                      hive_partitioning = true,
                      union_by_name   = true)
    GROUP BY _company
),
latest AS (
    SELECT *
    FROM (
        SELECT s.*,
               ROW_NUMBER() OVER (PARTITION BY s._company, s.ItemCode, s.CardCode ORDER BY s._extracted_at DESC) AS _rn
        FROM read_parquet('s3://{bucket}/raw/sap_b1/OSPP/**/*.parquet',
                      hive_partitioning = true,
                      union_by_name   = true) s
        JOIN newest_run n
          ON n._company = s._company
         AND regexp_replace(s._run_id, '-b[0-9]+$', '') = n._run_key
    )
    WHERE _rn = 1
)
SELECT
    CAST(ItemCode AS VARCHAR)                   AS item_code,
    CAST(CardCode AS VARCHAR)                   AS card_code,
    CAST(Price AS DECIMAL(19,6))                AS price,
    CAST(Currency AS VARCHAR)                   AS currency,
    CAST(Discount AS DECIMAL(19,6))             AS discount,
    CAST(ListNum AS BIGINT)                     AS list_num,
    CAST(Valid AS VARCHAR)                      AS valid,
    CAST(ValidFrom AS TIMESTAMP)                AS valid_from,
    CAST(ValidTo AS TIMESTAMP)                  AS valid_to,
    CAST(CreateDate AS TIMESTAMP)               AS create_date,
    CAST(UpdateDate AS TIMESTAMP)               AS update_date,
    _company                              AS company,
    load_date
FROM latest
ORDER BY company, item_code, card_code
