-- banxico_series_quality  (silver)  cartridge: banxico
-- sources: ["silver/banxico/banxico_series_metadata_latest", "silver/banxico/banxico_series_observations_normalized"]
-- description: Deterministic freshness, validity and confidence status for allowlisted Banxico series.
SELECT 'managed_by_banxico_materializer' AS note WHERE FALSE
