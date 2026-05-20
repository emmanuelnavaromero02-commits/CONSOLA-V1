-- replicon_project_detail_curated  (silver)  cartridge: replicon
-- sources: ["raw/replicon/ProjectDetail"]
-- description: Detalle de Proyecto

SELECT *
FROM read_parquet(
  's3://{bucket}/raw/replicon/ProjectDetail/**/*.parquet',
  hive_partitioning=true, union_by_name=true
)
