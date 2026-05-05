-- Track which datasets each analytic app reads (auto-extracted from HTML).
ALTER TABLE analytic_apps ADD COLUMN IF NOT EXISTS datasets_used TEXT[];

-- Backfill: extract /api/data/<dataset> usages from existing HTMLs.
UPDATE analytic_apps
   SET datasets_used = (
     SELECT array_agg(DISTINCT m[1])
       FROM regexp_matches(html, '/api/data/([a-zA-Z_][a-zA-Z0-9_]*)', 'g') AS m
   )
 WHERE datasets_used IS NULL OR cardinality(datasets_used) = 0;
