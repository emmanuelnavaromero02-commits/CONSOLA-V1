-- replicon_billingitem_latest  (silver)  cartridge: replicon
-- sources: ["raw/replicon/BillingItem"]
-- description: BillingItem - Ultima extraccion Replicon Bronze

SELECT * FROM read_parquet('s3://{bucket}/raw/replicon/BillingItem/**/*.parquet', hive_partitioning=true, union_by_name=true)
