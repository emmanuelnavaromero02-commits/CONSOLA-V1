-- Legacy migration kept for existing installations that passed through the
-- Gemini/768 embedding period. Migration 71 supersedes this and finishes the
-- schema at Titan v2 / vector(1024), clearing embeddings again for reindex.

DO $$
BEGIN
    IF EXISTS (
        SELECT 1
          FROM pg_attribute
         WHERE attrelid = 'rag_chunks'::regclass
           AND attname = 'embedding'
           AND atttypmod <> 768
    ) THEN
        UPDATE rag_chunks SET embedding = NULL WHERE embedding IS NOT NULL;
        ALTER TABLE rag_chunks
            ALTER COLUMN embedding TYPE vector(768)
            USING NULL::vector(768);
        UPDATE rag_sources SET chunk_count = 0, updated_at = NOW();
    END IF;
END $$;
