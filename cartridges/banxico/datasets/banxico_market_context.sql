-- banxico_market_context  (gold)  cartridge: banxico
-- sources: ["silver/banxico/banxico_series_quality", "silver/banxico/banxico_series_observations_normalized"]
-- description: Governed Banxico macro context with usability flags, provenance and confidence for downstream read-only consumption.
SELECT 'managed_by_banxico_materializer' AS note WHERE FALSE
