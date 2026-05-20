-- Align pgvector storage with Bedrock Titan Text Embeddings v2.
-- Titan v2 defaults to 1024 dimensions here. Existing 768-dimension Gemini
-- embeddings cannot be searched with 1024-dimension Bedrock queries, so reset
-- stored embeddings and require a RAG reindex after migration.

DO $$
BEGIN
    IF to_regclass('public.rag_chunks') IS NOT NULL
       AND EXISTS (
            SELECT 1
              FROM pg_attribute
             WHERE attrelid = 'public.rag_chunks'::regclass
               AND attname = 'embedding'
               AND atttypmod <> 1024
       )
    THEN
        UPDATE rag_chunks SET embedding = NULL WHERE embedding IS NOT NULL;
        ALTER TABLE rag_chunks
            ALTER COLUMN embedding TYPE vector(1024)
            USING NULL::vector(1024);
        IF to_regclass('public.rag_sources') IS NOT NULL THEN
            UPDATE rag_sources SET chunk_count = 0, updated_at = NOW();
        END IF;
    END IF;
END $$;
