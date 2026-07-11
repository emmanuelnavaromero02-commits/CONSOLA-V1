-- inegi_indicator_observations_normalized  (silver)  cartridge: inegi
-- sources: ["raw/inegi/series_observations"]
-- description: Normalized INEGI indicator observations with deterministic validation flags, sourced only from complete Bronze manifests.
SELECT 'managed_by_inegi_materializer' AS note WHERE FALSE
