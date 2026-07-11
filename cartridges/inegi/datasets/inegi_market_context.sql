-- inegi_market_context  (gold)  cartridge: inegi
-- sources: ["silver/inegi/inegi_indicator_quality", "silver/inegi/inegi_indicator_observations_normalized"]
-- description: Governed INEGI macro context with usability flags, provenance and confidence for downstream read-only consumption.
SELECT 'managed_by_inegi_materializer' AS note WHERE FALSE
