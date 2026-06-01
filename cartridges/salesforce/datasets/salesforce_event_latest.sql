-- salesforce_event_latest  (silver)  cartridge: salesforce
-- sources: ["raw/salesforce/Event"]
-- description: Última versión de cada evento / reunión (dedup por Id).
SELECT DISTINCT ON (Id)
    Id                              AS event_id,
    Subject                         AS subject,
    WhoId                           AS who_id,
    WhatId                          AS what_id,
    CAST(ActivityDate AS DATE)      AS activity_date,
    CAST(DurationInMinutes AS BIGINT) AS duration_minutes,
    OwnerId                         AS owner_id,
    load_date
FROM read_parquet('s3://{bucket}/raw/salesforce/Event/**/*.parquet',
                  hive_partitioning = true, union_by_name = true)
ORDER BY Id, load_date DESC
