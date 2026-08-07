-- ============================================================================
-- Corpus-KB Application Schema (migration 004)
-- ============================================================================
-- This is the schema the application code actually queries. Migrations
-- 001-003 created a separate, unused corpus.sources/corpus.nodes schema and
-- the corpus_rag namespace; this migration adds the real tables. See
-- src/storage/schema.sql for the annotated source of truth this is derived
-- from — keep both in sync if the schema changes.
-- ============================================================================

-- ============================================================================
-- 0. Set Session Context for RLS During Migration
-- ============================================================================
-- Set the default tenant context so RLS policies allow inserts. This is reset
-- at the end of the transaction automatically.

SET app.current_tenant_id = '00000000-0000-0000-0000-000000000001';

-- ============================================================================
-- 1. Extensions
-- ============================================================================

CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

-- ============================================================================
-- 2. Tenants Table
-- ============================================================================

CREATE TABLE IF NOT EXISTS tenants (
    tenant_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name VARCHAR(255) NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

INSERT INTO tenants (tenant_id, name)
VALUES ('00000000-0000-0000-0000-000000000001', 'default')
ON CONFLICT DO NOTHING;

ALTER TABLE tenants ENABLE ROW LEVEL SECURITY;

DO $$
BEGIN
    CREATE POLICY tenants_tenant_isolation ON tenants
        USING (tenant_id = NULLIF(current_setting('app.current_tenant_id', true), '')::UUID)
        WITH CHECK (tenant_id = NULLIF(current_setting('app.current_tenant_id', true), '')::UUID);
EXCEPTION WHEN OTHERS THEN
    RAISE WARNING 'Policy tenants_tenant_isolation already exists: %', SQLERRM;
END $$;

-- ============================================================================
-- 3. Documents Table (projection from DocumentIngested events)
-- ============================================================================

CREATE TABLE IF NOT EXISTS documents (
    doc_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID NOT NULL,
    source VARCHAR(1024) NOT NULL,
    source_type VARCHAR(50) NOT NULL DEFAULT 'text',
    chunk_count INT NOT NULL DEFAULT 0,
    file_size BIGINT,
    file_hash VARCHAR(64),
    language VARCHAR(50),
    metadata JSONB DEFAULT '{}',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (tenant_id, source)
);

CREATE INDEX idx_documents_tenant ON documents(tenant_id);
CREATE INDEX idx_documents_source ON documents(source);
CREATE INDEX idx_documents_hash ON documents(file_hash);

ALTER TABLE documents ENABLE ROW LEVEL SECURITY;

DO $$
BEGIN
    CREATE POLICY documents_tenant_isolation ON documents
        USING (tenant_id = NULLIF(current_setting('app.current_tenant_id', true), '')::UUID)
        WITH CHECK (tenant_id = NULLIF(current_setting('app.current_tenant_id', true), '')::UUID);
EXCEPTION WHEN OTHERS THEN
    RAISE WARNING 'Policy documents_tenant_isolation already exists: %', SQLERRM;
END $$;

-- ============================================================================
-- 4. Chunks Table (projection from ChunksAdded events — text only, no vectors)
-- ============================================================================

CREATE TABLE IF NOT EXISTS chunks (
    chunk_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID NOT NULL,
    doc_id UUID NOT NULL REFERENCES documents(doc_id) ON DELETE CASCADE,
    chunk_index INT NOT NULL,
    text TEXT NOT NULL,
    source_type VARCHAR(50),
    chunk_type VARCHAR(50),
    entity_name VARCHAR(255),
    heading_path JSONB,
    file_path VARCHAR(1024),
    start_line INT,
    end_line INT,
    metadata JSONB DEFAULT '{}',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (tenant_id, doc_id, chunk_index)
);

CREATE INDEX idx_chunks_tenant ON chunks(tenant_id);
CREATE INDEX idx_chunks_doc ON chunks(doc_id);
CREATE INDEX idx_chunks_tenant_doc ON chunks(tenant_id, doc_id);
CREATE INDEX idx_chunks_fts ON chunks USING gin (to_tsvector('english', text));

ALTER TABLE chunks ENABLE ROW LEVEL SECURITY;

DO $$
BEGIN
    CREATE POLICY chunks_tenant_isolation ON chunks
        USING (tenant_id = NULLIF(current_setting('app.current_tenant_id', true), '')::UUID)
        WITH CHECK (tenant_id = NULLIF(current_setting('app.current_tenant_id', true), '')::UUID);
EXCEPTION WHEN OTHERS THEN
    RAISE WARNING 'Policy chunks_tenant_isolation already exists: %', SQLERRM;
END $$;

-- ============================================================================
-- 5. Chunks_Vectors Table (async embedding projection — pgvector)
-- ============================================================================
-- Column width matches the DEFAULT configured model (nomic-embed-text,
-- 768d). pgvector rejects inserts that don't match the declared width
-- exactly, and caps ivfflat/hnsw indexing at 2000 dimensions — a column
-- wide enough for qwen3-embedding:8b-q8_0 (4096d) could never be indexed.
-- Switching the default model requires ALTER COLUMN vector TYPE vector(N)
-- and accepting sequential-scan search above 2000 dimensions.

CREATE TABLE IF NOT EXISTS chunks_vectors (
    chunk_id UUID PRIMARY KEY REFERENCES chunks(chunk_id) ON DELETE CASCADE,
    tenant_id UUID NOT NULL,
    vector vector(768),
    embedding_model VARCHAR(255) NOT NULL DEFAULT 'nomic-embed-text',
    embedded_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT vector_not_null CHECK (vector IS NOT NULL)
);

DO $$
BEGIN
    EXECUTE 'CREATE INDEX idx_chunks_vectors_ivfflat ON chunks_vectors USING ivfflat (vector vector_cosine_ops) WITH (lists = 100)';
EXCEPTION WHEN OTHERS THEN
    RAISE WARNING 'Skipping ivfflat index on chunks_vectors.vector: %', SQLERRM;
END $$;

DO $$
BEGIN
    EXECUTE 'CREATE INDEX IF NOT EXISTS idx_chunks_vectors_hnsw ON chunks_vectors USING hnsw (vector vector_cosine_ops) WITH (m = 16, ef_construction = 200)';
EXCEPTION WHEN OTHERS THEN
    RAISE WARNING 'Skipping hnsw index on chunks_vectors.vector: %', SQLERRM;
END $$;

CREATE INDEX idx_chunks_vectors_tenant ON chunks_vectors(tenant_id);
CREATE INDEX idx_chunks_vectors_model ON chunks_vectors(embedding_model);

ALTER TABLE chunks_vectors ENABLE ROW LEVEL SECURITY;

DO $$
BEGIN
    CREATE POLICY chunks_vectors_tenant_isolation ON chunks_vectors
        USING (tenant_id = NULLIF(current_setting('app.current_tenant_id', true), '')::UUID)
        WITH CHECK (tenant_id = NULLIF(current_setting('app.current_tenant_id', true), '')::UUID);
EXCEPTION WHEN OTHERS THEN
    RAISE WARNING 'Policy chunks_vectors_tenant_isolation already exists: %', SQLERRM;
END $$;

-- ============================================================================
-- 6. Entities Table (knowledge graph nodes)
-- ============================================================================

CREATE TABLE IF NOT EXISTS entities (
    entity_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID NOT NULL,
    name VARCHAR(255) NOT NULL,
    entity_type VARCHAR(100) NOT NULL DEFAULT 'concept',
    source_document_id UUID REFERENCES documents(doc_id) ON DELETE SET NULL,
    metadata JSONB DEFAULT '{}',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (tenant_id, name, entity_type)
);

CREATE INDEX idx_entities_tenant ON entities(tenant_id);
CREATE INDEX idx_entities_type ON entities(entity_type);
CREATE INDEX idx_entities_tenant_type ON entities(tenant_id, entity_type);
CREATE INDEX idx_entities_name ON entities(name);

ALTER TABLE entities ENABLE ROW LEVEL SECURITY;

DO $$
BEGIN
    CREATE POLICY entities_tenant_isolation ON entities
        USING (tenant_id = NULLIF(current_setting('app.current_tenant_id', true), '')::UUID)
        WITH CHECK (tenant_id = NULLIF(current_setting('app.current_tenant_id', true), '')::UUID);
EXCEPTION WHEN OTHERS THEN
    RAISE WARNING 'Policy entities_tenant_isolation already exists: %', SQLERRM;
END $$;

-- ============================================================================
-- 7. Relations Table (knowledge graph edges)
-- ============================================================================

CREATE TABLE IF NOT EXISTS relations (
    relation_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID NOT NULL,
    source_entity_id UUID NOT NULL REFERENCES entities(entity_id) ON DELETE CASCADE,
    target_entity_id UUID NOT NULL REFERENCES entities(entity_id) ON DELETE CASCADE,
    relation_type VARCHAR(100) NOT NULL DEFAULT 'related_to',
    weight FLOAT NOT NULL DEFAULT 1.0,
    metadata JSONB DEFAULT '{}',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (tenant_id, source_entity_id, target_entity_id, relation_type)
);

CREATE INDEX idx_relations_tenant ON relations(tenant_id);
CREATE INDEX idx_relations_source ON relations(source_entity_id);
CREATE INDEX idx_relations_target ON relations(target_entity_id);
CREATE INDEX idx_relations_tenant_source ON relations(tenant_id, source_entity_id);
CREATE INDEX idx_relations_tenant_target ON relations(tenant_id, target_entity_id);

ALTER TABLE relations ENABLE ROW LEVEL SECURITY;

DO $$
BEGIN
    CREATE POLICY relations_tenant_isolation ON relations
        USING (tenant_id = NULLIF(current_setting('app.current_tenant_id', true), '')::UUID)
        WITH CHECK (tenant_id = NULLIF(current_setting('app.current_tenant_id', true), '')::UUID);
EXCEPTION WHEN OTHERS THEN
    RAISE WARNING 'Policy relations_tenant_isolation already exists: %', SQLERRM;
END $$;

-- ============================================================================
-- 8. Projection Checkpoints (catch-up subscription state)
-- ============================================================================

CREATE TABLE IF NOT EXISTS projection_checkpoints (
    projection_name VARCHAR(255) NOT NULL,
    tenant_id UUID NOT NULL,
    last_event_id UUID NOT NULL,
    last_event_timestamp TIMESTAMPTZ NOT NULL,
    checkpoint_timestamp TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (projection_name, tenant_id)
);

CREATE INDEX idx_checkpoints_tenant ON projection_checkpoints(tenant_id);

ALTER TABLE projection_checkpoints ENABLE ROW LEVEL SECURITY;

DO $$
BEGIN
    CREATE POLICY checkpoints_tenant_isolation ON projection_checkpoints
        USING (tenant_id = NULLIF(current_setting('app.current_tenant_id', true), '')::UUID)
        WITH CHECK (tenant_id = NULLIF(current_setting('app.current_tenant_id', true), '')::UUID);
EXCEPTION WHEN OTHERS THEN
    RAISE WARNING 'Policy checkpoints_tenant_isolation already exists: %', SQLERRM;
END $$;

-- ============================================================================
-- 9. Projection DLQ (Dead-Letter Queue for failed projections)
-- ============================================================================

CREATE TABLE IF NOT EXISTS projection_dlq (
    dlq_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    projection_name VARCHAR(255) NOT NULL,
    tenant_id UUID NOT NULL,
    event_id UUID NOT NULL,
    event_type VARCHAR(255) NOT NULL,
    error_message TEXT NOT NULL,
    error_stacktrace TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    retry_count INT NOT NULL DEFAULT 0,
    resolved BOOLEAN NOT NULL DEFAULT FALSE,
    UNIQUE (projection_name, tenant_id, event_id)
);

CREATE INDEX idx_dlq_tenant_projection ON projection_dlq(tenant_id, projection_name);
CREATE INDEX idx_dlq_created ON projection_dlq(created_at DESC);
CREATE INDEX idx_dlq_unresolved ON projection_dlq(tenant_id, resolved) WHERE resolved = FALSE;

ALTER TABLE projection_dlq ENABLE ROW LEVEL SECURITY;

DO $$
BEGIN
    CREATE POLICY dlq_tenant_isolation ON projection_dlq
        USING (tenant_id = NULLIF(current_setting('app.current_tenant_id', true), '')::UUID)
        WITH CHECK (tenant_id = NULLIF(current_setting('app.current_tenant_id', true), '')::UUID);
EXCEPTION WHEN OTHERS THEN
    RAISE WARNING 'Policy dlq_tenant_isolation already exists: %', SQLERRM;
END $$;

-- ============================================================================
-- 10. Idempotency Keys (command deduplication)
-- ============================================================================

CREATE TABLE IF NOT EXISTS idempotency_keys (
    idempotency_key VARCHAR(255) NOT NULL,
    tenant_id UUID NOT NULL,
    command_type VARCHAR(255) NOT NULL,
    command_payload JSONB NOT NULL,
    result JSONB,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    expires_at TIMESTAMPTZ NOT NULL DEFAULT NOW() + INTERVAL '24 hours',
    PRIMARY KEY (idempotency_key, tenant_id)
);

CREATE INDEX idx_idempotency_expires ON idempotency_keys(expires_at);
CREATE INDEX idx_idempotency_tenant ON idempotency_keys(tenant_id);

ALTER TABLE idempotency_keys ENABLE ROW LEVEL SECURITY;

DO $$
BEGIN
    CREATE POLICY idempotency_tenant_isolation ON idempotency_keys
        USING (tenant_id = NULLIF(current_setting('app.current_tenant_id', true), '')::UUID)
        WITH CHECK (tenant_id = NULLIF(current_setting('app.current_tenant_id', true), '')::UUID);
EXCEPTION WHEN OTHERS THEN
    RAISE WARNING 'Policy idempotency_tenant_isolation already exists: %', SQLERRM;
END $$;

-- ============================================================================
-- 11. Tags Table
-- ============================================================================

CREATE TABLE IF NOT EXISTS tags (
    tag_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID NOT NULL,
    name VARCHAR(255) NOT NULL,
    color VARCHAR(50),
    description TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE(tenant_id, name)
);

CREATE INDEX idx_tags_tenant ON tags(tenant_id);
CREATE INDEX idx_tags_name ON tags(name);

ALTER TABLE tags ENABLE ROW LEVEL SECURITY;
DO $$
BEGIN
    CREATE POLICY tags_tenant_isolation ON tags
        USING (tenant_id = NULLIF(current_setting('app.current_tenant_id', true), '')::UUID)
        WITH CHECK (tenant_id = NULLIF(current_setting('app.current_tenant_id', true), '')::UUID);
EXCEPTION WHEN OTHERS THEN
    RAISE WARNING 'Policy tags_tenant_isolation already exists: %', SQLERRM;
END $$;

-- ============================================================================
-- 12. Document Tags (many-to-many)
-- ============================================================================

CREATE TABLE IF NOT EXISTS document_tags (
    doc_id UUID NOT NULL REFERENCES documents(doc_id) ON DELETE CASCADE,
    tenant_id UUID NOT NULL,
    tag_id UUID NOT NULL REFERENCES tags(tag_id) ON DELETE CASCADE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY(doc_id, tag_id)
);

CREATE INDEX idx_document_tags_tenant ON document_tags(tenant_id);
CREATE INDEX idx_document_tags_doc ON document_tags(doc_id);

ALTER TABLE document_tags ENABLE ROW LEVEL SECURITY;
DO $$
BEGIN
    CREATE POLICY document_tags_tenant_isolation ON document_tags
        USING (tenant_id = NULLIF(current_setting('app.current_tenant_id', true), '')::UUID)
        WITH CHECK (tenant_id = NULLIF(current_setting('app.current_tenant_id', true), '')::UUID);
EXCEPTION WHEN OTHERS THEN
    RAISE WARNING 'Policy document_tags_tenant_isolation already exists: %', SQLERRM;
END $$;

-- ============================================================================
-- 13. Metadata (key-value store)
-- ============================================================================

CREATE TABLE IF NOT EXISTS metadata (
    key VARCHAR(255) NOT NULL,
    value TEXT,
    doc_id UUID REFERENCES documents(doc_id) ON DELETE CASCADE,
    tenant_id UUID NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY(key, tenant_id, doc_id)
);

CREATE INDEX idx_metadata_tenant ON metadata(tenant_id);
CREATE INDEX idx_metadata_key ON metadata(key);

ALTER TABLE metadata ENABLE ROW LEVEL SECURITY;
DO $$
BEGIN
    CREATE POLICY metadata_tenant_isolation ON metadata
        USING (tenant_id = NULLIF(current_setting('app.current_tenant_id', true), '')::UUID)
        WITH CHECK (tenant_id = NULLIF(current_setting('app.current_tenant_id', true), '')::UUID);
EXCEPTION WHEN OTHERS THEN
    RAISE WARNING 'Policy metadata_tenant_isolation already exists: %', SQLERRM;
END $$;
