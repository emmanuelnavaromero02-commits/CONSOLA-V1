-- replicon_department_latest  (silver)  cartridge: replicon
-- sources: ["raw/replicon/Department"]
-- description: Department - Ultima extraccion Replicon Bronze

SELECT * FROM read_parquet('s3://{bucket}/raw/replicon/Department/**/*.parquet', hive_partitioning=true, union_by_name=true)
