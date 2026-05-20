-- replicon_activity_latest  (silver)  cartridge: replicon
-- sources: ["raw/replicon/Activity"]
-- description: Activity - Ultima extraccion Replicon Bronze

SELECT * FROM read_parquet('s3://{bucket}/raw/replicon/Activity/**/*.parquet', hive_partitioning=true, union_by_name=true)
