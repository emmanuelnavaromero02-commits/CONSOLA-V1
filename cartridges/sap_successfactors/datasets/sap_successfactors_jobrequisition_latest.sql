-- sap_successfactors_jobrequisition_latest  (silver)  cartridge: sap_successfactors
-- sources: ["raw/sap_successfactors/JobRequisition"]
-- description: Última extracción de requisiciones de empleo (Recruiting).

WITH latest AS (
    SELECT *
    FROM read_parquet('s3://{bucket}/raw/sap_successfactors/JobRequisition/**/*.parquet',
                      hive_partitioning = true,
                      union_by_name   = true)
    WHERE load_date = (SELECT MAX(load_date)
                       FROM read_parquet('s3://{bucket}/raw/sap_successfactors/JobRequisition/**/*.parquet',
                                          hive_partitioning = true))
)
SELECT
    jobReqId             AS job_req_id,
    jobTitle             AS job_title,
    status               AS status,
    department           AS department,
    location             AS location,
    load_date
FROM latest
ORDER BY job_req_id
