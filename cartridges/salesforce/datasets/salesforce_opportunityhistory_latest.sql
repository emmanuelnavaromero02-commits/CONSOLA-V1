-- salesforce_opportunityhistory_latest  (silver)  cartridge: salesforce
-- sources: ["raw/salesforce/OpportunityHistory"]
-- description: Historial de etapas por oportunidad (append-only; dedup por Id de la fila de historial).
SELECT DISTINCT ON (Id)
    Id                          AS history_id,
    OpportunityId               AS opportunity_id,
    StageName                   AS stage_name,
    CAST(Amount AS DOUBLE)      AS amount,
    CAST(Probability AS DOUBLE) AS probability,
    CAST(CloseDate AS DATE)     AS close_date,
    CAST(CreatedDate AS TIMESTAMP) AS created_at,
    load_date
FROM read_parquet('s3://{bucket}/raw/salesforce/OpportunityHistory/**/*.parquet',
                  hive_partitioning = true, union_by_name = true)
ORDER BY Id, load_date DESC
