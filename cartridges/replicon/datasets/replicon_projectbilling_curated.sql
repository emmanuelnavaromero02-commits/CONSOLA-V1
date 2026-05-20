-- replicon_projectbilling_curated  (silver)  cartridge: replicon
-- sources: []
-- description: 

SELECT
  COALESCE(NULLIF(CAST("Referencia" AS VARCHAR), ''), '0') AS "Project Code",

  CASE
    WHEN "Total USD" IS NOT NULL AND "Total USD" <> 0 THEN 'USD$'
    ELSE 'MXN'
  END AS "Moneda",

  COALESCE("Precio Unitario USD", "Precio Unitario MXN") AS "Precio Unitario",
  COALESCE("Subtotal USD",        "Subtotal MXN")        AS "Subtotal",
  COALESCE("IVA USD",             "IVA MXN")             AS "IVA",
  COALESCE("Total USD",           "Total MXN")           AS "Total",

  * EXCLUDE (
    "Referencia",
    "Precio Unitario USD", "Subtotal USD", "IVA USD", "Total USD",
    "Precio Unitario MXN", "Subtotal MXN", "IVA MXN", "Total MXN",
    "Unnamed: 24","Unnamed: 25","Unnamed: 26","Unnamed: 27","Unnamed: 28",
    "Unnamed: 29","Unnamed: 30","Unnamed: 31"
  )

FROM read_parquet(
  's3://{bucket}/raw/replicon/ProjectBilling/**/*.parquet',
  hive_partitioning=true, union_by_name=true
)
