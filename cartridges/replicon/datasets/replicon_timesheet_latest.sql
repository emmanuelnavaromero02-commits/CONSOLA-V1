-- replicon_timesheet_latest  (silver)  cartridge: replicon
-- sources: ["raw/replicon/Timesheet"]
-- description: Timesheet - Ultima extraccion Replicon Bronze

SELECT * FROM read_parquet('s3://{bucket}/raw/replicon/Timesheet/**/*.parquet', hive_partitioning=true, union_by_name=true)
