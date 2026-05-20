-- Keep pgvector storage aligned with the active Gemini embedder.
-- Existing volumes created from older Bedrock/Titan defaults may have
-- rag_chunks.embedding as vector(1024). Those vectors cannot be searched with
-- 768-dimension Gemini queries, so reset embeddings and require reindex.

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
