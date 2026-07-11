-- sec_company_quality  (silver)  cartridge: sec_edgar
-- sources: ["silver/sec_edgar/sec_company_metadata_latest", "silver/sec_edgar/sec_company_facts_normalized"]
-- description: Deterministic freshness, validity and confidence status for allowlisted SEC company facts.
SELECT 'managed_by_sec_edgar_materializer' AS note WHERE FALSE
