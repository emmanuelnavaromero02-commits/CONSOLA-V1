-- inegi_indicator_quality  (silver)  cartridge: inegi
-- sources: ["silver/inegi/inegi_indicator_metadata_latest", "silver/inegi/inegi_indicator_observations_normalized"]
-- description: Deterministic freshness, validity and confidence status for allowlisted INEGI indicators.
SELECT 'managed_by_inegi_materializer' AS note WHERE FALSE
