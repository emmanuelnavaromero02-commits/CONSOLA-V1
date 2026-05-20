-- replicon_project_latest  (silver)  cartridge: replicon
-- sources: ["raw/replicon/Project"]
-- description: Project - Ultima extraccion Replicon Bronze

SELECT * FROM read_parquet('s3://{bucket}/raw/replicon/Project/**/*.parquet', hive_partitioning=true, union_by_name=true)
