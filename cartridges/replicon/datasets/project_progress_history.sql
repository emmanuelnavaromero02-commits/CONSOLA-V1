-- project_progress_history  (silver)  cartridge: replicon
-- sources: ["raw/replicon/ProjectAudit"]
-- description: Historial de cambios en % Plan Progress y % Real Progress por proyecto. Una fila por evento de cambio con incremento respecto al valor anterior.

WITH src AS (
  SELECT DISTINCT
    "Project Code"    AS project_code,
    "Project Name"    AS project_name,
    "Project Manager" AS project_manager,
    "Field"           AS field,
    "Original Value"  AS prev_value,
    "New Value"       AS new_value,
    ROUND("New Value" - COALESCE("Original Value", 0), 4) AS increment,
    "Modified By"     AS modified_by,
    TRY_STRPTIME("Modified On", '%d/%m/%Y %I:%M:%S %p') AS modified_at
  FROM read_parquet('s3://{bucket}/raw/replicon/ProjectAudit/load_date=*/data.parquet',
    hive_partitioning=true, union_by_name=true)
  WHERE "Field" IN ('% Plan Progress', '% Real Progress')
    AND "New Value" IS NOT NULL
)
SELECT
  project_code,
  project_name,
  project_manager,
  modified_at,
  modified_by,
  MAX(CASE WHEN field = '% Plan Progress' THEN prev_value END)  AS plan_progress_prev,
  MAX(CASE WHEN field = '% Plan Progress' THEN new_value END)   AS plan_progress,
  MAX(CASE WHEN field = '% Plan Progress' THEN increment END)   AS plan_progress_increment,
  MAX(CASE WHEN field = '% Real Progress' THEN prev_value END)  AS real_progress_prev,
  MAX(CASE WHEN field = '% Real Progress' THEN new_value END)   AS real_progress,
  MAX(CASE WHEN field = '% Real Progress' THEN increment END)   AS real_progress_increment
FROM src
GROUP BY project_code, project_name, project_manager, modified_at, modified_by
ORDER BY project_code, modified_at
