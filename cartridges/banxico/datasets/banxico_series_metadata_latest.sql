-- banxico_series_metadata_latest  (silver)  cartridge: banxico
-- sources: ["raw/banxico/series_metadata"]
-- description: Latest official Banxico metadata for the allowlisted SIE series, sourced only from complete Bronze manifests.
SELECT 'managed_by_banxico_materializer' AS note WHERE FALSE
