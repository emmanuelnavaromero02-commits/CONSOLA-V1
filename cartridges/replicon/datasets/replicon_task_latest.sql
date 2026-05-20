-- replicon_task_latest  (silver)  cartridge: replicon
-- sources: ["raw/replicon/Task"]
-- description: Task - Ultima extraccion Replicon Bronze

SELECT * FROM read_parquet('s3://{bucket}/raw/replicon/Task/**/*.parquet', hive_partitioning=true, union_by_name=true)
