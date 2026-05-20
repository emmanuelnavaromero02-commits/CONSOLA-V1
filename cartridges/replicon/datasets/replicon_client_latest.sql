-- replicon_client_latest  (silver)  cartridge: replicon
-- sources: ["raw/replicon/Client"]
-- description: Client - Ultima extraccion Replicon Bronze

SELECT * FROM read_parquet('s3://{bucket}/raw/replicon/Client/**/*.parquet', hive_partitioning=true, union_by_name=true)
