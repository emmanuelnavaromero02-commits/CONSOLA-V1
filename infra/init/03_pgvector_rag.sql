-- RAG Knowledge Base schema
-- Requires: pgvector/pgvector:pg15 image (has the extension pre-installed)
-- If upgrading from postgres:15, run manually:
--   psql -U postgres -d modecissions -c "CREATE EXTENSION IF NOT EXISTS vector;"

CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS rag_sources (
    id          SERIAL PRIMARY KEY,
    name        TEXT NOT NULL,
    description TEXT,
    mime_type   TEXT DEFAULT 'text/plain',
    size_chars  INTEGER DEFAULT 0,
    chunk_count INTEGER DEFAULT 0,
    created_at  TIMESTAMPTZ DEFAULT NOW(),
    updated_at  TIMESTAMPTZ DEFAULT NOW(),
    CONSTRAINT rag_sources_name_uq UNIQUE (name)
);

-- Parent chunks: large context (~3000 chars) returned to LLM after retrieval
-- Child chunks: small (~600 chars) used for HNSW ANN search
CREATE TABLE IF NOT EXISTS rag_chunks (
    id          BIGSERIAL PRIMARY KEY,
    source_id   INTEGER NOT NULL REFERENCES rag_sources(id) ON DELETE CASCADE,
    chunk_type  TEXT NOT NULL CHECK (chunk_type IN ('parent', 'child')),
    parent_id   BIGINT REFERENCES rag_chunks(id) ON DELETE CASCADE,
    chunk_index INTEGER NOT NULL,
    content     TEXT NOT NULL,
    embedding   vector(768),   -- text-embedding-004 (Gemini); change EMBED_DIM env if needed
    metadata    JSONB DEFAULT '{}',
    created_at  TIMESTAMPTZ DEFAULT NOW()
);

-- HNSW index — only on child chunks (where embeddings live)
-- m=16, ef_construction=64 balances build speed vs recall
CREATE INDEX IF NOT EXISTS rag_chunks_hnsw_idx
    ON rag_chunks USING hnsw (embedding vector_cosine_ops)
    WITH (m = 16, ef_construction = 64)
    WHERE chunk_type = 'child';

CREATE INDEX IF NOT EXISTS rag_chunks_source_idx ON rag_chunks(source_id);
CREATE INDEX IF NOT EXISTS rag_chunks_parent_idx ON rag_chunks(parent_id);
CREATE INDEX IF NOT EXISTS rag_chunks_type_idx   ON rag_chunks(chunk_type);
