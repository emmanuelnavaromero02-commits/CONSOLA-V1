-- Shared normalization for every Talent score.
--
-- TRY_CAST only rejects what cannot be parsed: -1.0, NaN, Infinity and 1000.0
-- all survive it, and the banding downstream turns them into a real box.
-- Infinity/20 clears the high threshold, 1000/20 = 50 clears it too, and NaN
-- compares false against every bound so it falls through to the lowest band.
--
-- Every Talent dataset routes its raw scores through these macros instead, so
-- finiteness and the documented domain are checked once, before any band, box,
-- candidate or signal is computed.
--
-- Documented domain: 0..5 on the competency scale, or 0..100 as a percentage
-- which is rescaled by 20. Zero is a legitimate value and is preserved; the
-- boundaries 5 and 100 are legitimate and map to 5.0.

CREATE OR REPLACE MACRO talent_score_is_valid(raw) AS (
    TRY_CAST(raw AS DOUBLE) IS NOT NULL
    AND isfinite(TRY_CAST(raw AS DOUBLE))
    AND TRY_CAST(raw AS DOUBLE) >= 0
    AND TRY_CAST(raw AS DOUBLE) <= 100
);

CREATE OR REPLACE MACRO talent_score_scale(raw) AS (
    CASE
        WHEN NOT talent_score_is_valid(raw) THEN NULL
        WHEN TRY_CAST(raw AS DOUBLE) > 5
            THEN TRY_CAST(raw AS DOUBLE) / 20.0
        ELSE TRY_CAST(raw AS DOUBLE)
    END
);
