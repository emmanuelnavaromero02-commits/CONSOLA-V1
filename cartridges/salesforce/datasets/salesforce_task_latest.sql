-- salesforce_task_latest  (silver)  cartridge: salesforce
-- sources: ["raw/salesforce/Task"]
-- description: Última versión de cada tarea / actividad (dedup por Id).
SELECT DISTINCT ON (Id)
    Id                        AS task_id,
    Subject                   AS subject,
    WhoId                     AS who_id,
    WhatId                    AS what_id,
    Status                    AS status,
    Priority                  AS priority,
    CAST(ActivityDate AS DATE) AS activity_date,
    CAST(IsClosed AS BOOLEAN) AS is_closed,
    OwnerId                   AS owner_id,
    load_date
FROM read_parquet('s3://{bucket}/raw/salesforce/Task/**/*.parquet',
                  hive_partitioning = true, union_by_name = true)
ORDER BY Id, load_date DESC
