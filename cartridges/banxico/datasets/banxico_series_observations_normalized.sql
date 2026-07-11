-- banxico_series_observations_normalized  (silver)  cartridge: banxico
-- sources: ["raw/banxico/series_observations"]
-- description: Normalized Banxico observations with deterministic validation flags, sourced only from complete Bronze manifests.
SELECT 'managed_by_banxico_materializer' AS note WHERE FALSE
