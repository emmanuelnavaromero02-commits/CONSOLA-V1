-- Canonical Talent score normalization registered by Refinement on every
-- DuckDB connection. Scores use either the 0..5 competency scale or 0..100
-- percentages. Zero and both upper bounds are valid; non-finite, negative and
-- out-of-domain values become NULL before any fit, band or box is derived.

CREATE OR REPLACE MACRO talent_score_is_valid(raw) AS (
    (
        typeof(raw) IN (
            'TINYINT', 'SMALLINT', 'INTEGER', 'BIGINT', 'HUGEINT',
            'UTINYINT', 'USMALLINT', 'UINTEGER', 'UBIGINT', 'UHUGEINT',
            'REAL', 'FLOAT', 'DOUBLE'
        )
        OR starts_with(typeof(raw), 'DECIMAL(')
    )
    AND CASE
        WHEN starts_with(typeof(raw), 'DECIMAL(')
            THEN regexp_full_match(
                CAST(raw AS VARCHAR),
                '(?:[0-9]{1,2}(?:[.][0-9]+)?|100(?:[.]0+)?)'
            )
        ELSE TRY_CAST(raw AS DOUBLE) IS NOT NULL
            AND isfinite(TRY_CAST(raw AS DOUBLE))
            AND TRY_CAST(raw AS DOUBLE) >= 0
            AND TRY_CAST(raw AS DOUBLE) <= 100
    END
);

CREATE OR REPLACE MACRO talent_score_scale(raw) AS (
    CASE
        WHEN NOT talent_score_is_valid(raw) THEN NULL
        WHEN TRY_CAST(raw AS DOUBLE) > 5
            THEN TRY_CAST(raw AS DOUBLE) / 20.0
        ELSE TRY_CAST(raw AS DOUBLE)
    END
);

-- Once a source has been materialized into the Talent profile its scores are
-- percentages.  Keep that domain explicit: a legitimate 5% must remain 5%,
-- never be reinterpreted as a 5/5 rating.
CREATE OR REPLACE MACRO talent_percent_is_valid(raw) AS (
    talent_score_is_valid(raw)
);

CREATE OR REPLACE MACRO talent_percent_scale(raw) AS (
    CASE
        WHEN NOT talent_percent_is_valid(raw) THEN NULL
        ELSE TRY_CAST(raw AS DOUBLE) / 20.0
    END
);
