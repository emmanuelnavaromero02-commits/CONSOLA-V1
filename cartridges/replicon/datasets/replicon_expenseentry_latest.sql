-- replicon_expenseentry_latest  (silver)  cartridge: replicon
-- sources: ["raw/replicon/ExpenseEntry"]
-- description: ExpenseEntry - Ultima extraccion Replicon Bronze

SELECT * FROM read_parquet('s3://{bucket}/raw/replicon/ExpenseEntry/**/*.parquet', hive_partitioning=true, union_by_name=true)
