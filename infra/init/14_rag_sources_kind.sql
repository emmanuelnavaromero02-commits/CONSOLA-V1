-- Tag each RAG source with a kind so the assistant (and the studio UI) can
-- separate auto-indexed dataset/column metadata from user-uploaded reports.

ALTER TABLE rag_sources
    ADD COLUMN IF NOT EXISTS kind TEXT NOT NULL DEFAULT 'document';

CREATE INDEX IF NOT EXISTS rag_sources_kind_idx ON rag_sources(kind);

-- Backfill: anything bootstrapped from datasets/raw uses prefixes 'dataset:' /
-- 'raw:' / 'master:' / 'silver:' / 'gold:' — mark those as schema, leave the
-- rest as document.
UPDATE rag_sources SET kind = 'schema'
 WHERE kind = 'document'
   AND (   name LIKE 'dataset:%'
        OR name LIKE 'raw:%'
        OR name LIKE 'silver:%'
        OR name LIKE 'master:%'
        OR name LIKE 'gold:%');
