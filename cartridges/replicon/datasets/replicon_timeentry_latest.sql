-- replicon_timeentry_latest  (silver)  cartridge: replicon
-- sources: ["raw/replicon/TimeEntry"]
-- description: TimeEntry - Ultima extraccion Replicon Bronze

SELECT * FROM read_parquet('s3://{bucket}/raw/replicon/TimeEntry/**/*.parquet', hive_partitioning=true, union_by_name=true)
