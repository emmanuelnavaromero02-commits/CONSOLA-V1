-- sap_b1_cinf_latest  (silver)  cartridge: sap_b1
-- sources: ["raw/sap_b1/CINF"]
-- description: Company database version and name (one row per company).

WITH newest_run AS (
    -- Instantánea sin marca de agua: la corrida más reciente POR EMPRESA
    -- (todos sus lotes) y nada más. Deduplicar el histórico resucitaría
    -- filas que la fuente borró; quedarse con MAX(load_date) mezclaría dos
    -- corridas del mismo día.
    SELECT _company,
           arg_max(regexp_replace(_run_id, '-b[0-9]+$', ''), _extracted_at) AS _run_key
    FROM read_parquet('s3://{bucket}/raw/sap_b1/CINF/**/*.parquet',
                      hive_partitioning = true,
                      union_by_name   = true)
    GROUP BY _company
),
latest AS (
    SELECT *
    FROM (
        SELECT s.*,
               1 AS _rn
        FROM read_parquet('s3://{bucket}/raw/sap_b1/CINF/**/*.parquet',
                      hive_partitioning = true,
                      union_by_name   = true) s
        JOIN newest_run n
          ON n._company = s._company
         AND regexp_replace(s._run_id, '-b[0-9]+$', '') = n._run_key
    )
    WHERE _rn = 1
)
SELECT
    CAST(Version AS BIGINT)                     AS version,
    CAST(CompnyName AS VARCHAR)                 AS compny_name,
    _company                              AS company,
    load_date
FROM latest
ORDER BY company
