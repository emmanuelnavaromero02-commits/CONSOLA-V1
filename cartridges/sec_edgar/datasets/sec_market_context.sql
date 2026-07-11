-- sec_market_context  (gold)  cartridge: sec_edgar
-- sources: ["silver/sec_edgar/sec_company_quality", "silver/sec_edgar/sec_company_facts_normalized"]
-- description: Governed SEC company context with usability flags, provenance and confidence.
SELECT 'managed_by_sec_edgar_materializer' AS note WHERE FALSE
