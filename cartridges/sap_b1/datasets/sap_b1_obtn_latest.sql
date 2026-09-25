-- sap_b1_obtn_latest  (silver)  cartridge: sap_b1
-- sources: ["raw/sap_b1/OBTN"]
-- description: Batch master (DistNumber, manufacture and expiry dates).

WITH newest_run AS (
    SELECT _company,
           arg_max(regexp_replace(_run_id, '-b[0-9]+$', ''), _extracted_at) AS _run_key
    FROM read_parquet('s3://{bucket}/raw/sap_b1/OBTN/**/*.parquet',
                      hive_partitioning = true,
                      union_by_name   = true)
    GROUP BY _company
),
latest AS (
    SELECT *
    FROM (
        SELECT s.*,
               ROW_NUMBER() OVER (PARTITION BY s._company, s.AbsEntry ORDER BY s._extracted_at DESC) AS _rn
        FROM read_parquet('s3://{bucket}/raw/sap_b1/OBTN/**/*.parquet',
                      hive_partitioning = true,
                      union_by_name   = true) s
        JOIN newest_run n
          ON n._company = s._company
         AND regexp_replace(s._run_id, '-b[0-9]+$', '') = n._run_key
    )
    WHERE _rn = 1
)
SELECT
    CAST(AbsEntry AS BIGINT)                    AS abs_entry,
    CAST(ItemCode AS VARCHAR)                   AS item_code,
    CAST(SysNumber AS BIGINT)                   AS sys_number,
    CAST(DistNumber AS VARCHAR)                 AS dist_number,
    CAST(MnfDate AS TIMESTAMP)                  AS mnf_date,
    CAST(ExpDate AS TIMESTAMP)                  AS exp_date,
    CAST(InDate AS TIMESTAMP)                   AS in_date,
    CAST(Status AS BIGINT)                      AS status,
    _company                              AS company,
    load_date
FROM latest
ORDER BY company, abs_entry
