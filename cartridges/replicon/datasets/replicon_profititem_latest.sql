-- replicon_profititem_latest  (silver)  cartridge: replicon
-- sources: ["raw/replicon/ProfitItem"]
-- description: ProfitItem - Ultima extraccion Replicon Bronze

SELECT * FROM read_parquet('s3://{bucket}/raw/replicon/ProfitItem/**/*.parquet', hive_partitioning=true, union_by_name=true)
