-- sec_company_metadata_latest  (silver)  cartridge: sec_edgar
-- sources: ["raw/sec_edgar/company_metadata"]
-- description: Latest official SEC EDGAR metadata for allowlisted CIKs, sourced only from complete Bronze manifests.
SELECT 'managed_by_sec_edgar_materializer' AS note WHERE FALSE
