-- replicon_resourcerequest_latest  (silver)  cartridge: replicon
-- sources: ["raw/replicon/ResourceRequest"]
-- description: ResourceRequest - Ultima extraccion Replicon Bronze

SELECT * FROM read_parquet('s3://{bucket}/raw/replicon/ResourceRequest/**/*.parquet', hive_partitioning=true, union_by_name=true)
