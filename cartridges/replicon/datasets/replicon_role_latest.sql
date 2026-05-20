-- replicon_role_latest  (silver)  cartridge: replicon
-- sources: ["raw/replicon/Role"]
-- description: Role - Ultima extraccion Replicon Bronze

SELECT * FROM read_parquet('s3://{bucket}/raw/replicon/Role/**/*.parquet', hive_partitioning=true, union_by_name=true)
