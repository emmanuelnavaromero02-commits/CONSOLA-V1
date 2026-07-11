-- inegi_indicator_metadata_latest  (silver)  cartridge: inegi
-- sources: ["raw/inegi/series_metadata"]
-- description: Latest official INEGI metadata for allowlisted indicators, sourced only from complete Bronze manifests.
SELECT 'managed_by_inegi_materializer' AS note WHERE FALSE
