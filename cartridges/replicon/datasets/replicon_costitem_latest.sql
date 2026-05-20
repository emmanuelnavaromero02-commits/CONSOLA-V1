-- replicon_costitem_latest  (silver)  cartridge: replicon
-- sources: ["raw/replicon/CostItem"]
-- description: CostItem - Ultima extraccion Replicon Bronze

SELECT * FROM read_parquet('s3://{bucket}/raw/replicon/CostItem/**/*.parquet', hive_partitioning=true, union_by_name=true)
