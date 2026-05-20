-- replicon_invoiceitem_latest  (silver)  cartridge: replicon
-- sources: ["raw/replicon/InvoiceItem"]
-- description: InvoiceItem - Ultima extraccion Replicon Bronze

SELECT * FROM read_parquet('s3://{bucket}/raw/replicon/InvoiceItem/**/*.parquet', hive_partitioning=true, union_by_name=true)
