-- sec_company_facts_normalized  (silver)  cartridge: sec_edgar
-- sources: ["raw/sec_edgar/company_facts"]
-- description: Normalized SEC XBRL company facts with deterministic validation flags.
SELECT 'managed_by_sec_edgar_materializer' AS note WHERE FALSE
