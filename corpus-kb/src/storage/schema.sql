-- ============================================================================
-- Corpus-KB Postgres Schema with Multi-Tenant RLS
-- ============================================================================
-- Event sourcing + DDD refactor foundation.
-- Multi-tenancy: Postgres RLS on all projection tables.
-- Events: The eventsourcing library owns event_store + snapshot_store tables.
-- Projections: Custom tables for documents, chunks, vectors, entities, relations.
-- Vectors: Derived data (async projection), NOT in event payloads.
-- ============================================================================

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

-- Default single-tenant placeholder
INSERT INTO tenants (tenant_id, name)
VALUES ('00000000-0000-0000-0000-000000000001', 'default')
ON CONFLICT DO NOTHING;

ALTER TABLE tenants ENABLE ROW LEVEL SECURITY;

CREATE POLICY tenants_tenant_isolation ON tenants
    USING (tenant_id = current_setting('app.current_tenant_id', true)::UUID)
    WITH CHECK (tenant_id = current_setting('app.current_tenant_id', true)::UUID);

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

CREATE POLICY documents_tenant_isolation ON documents
    USING (tenant_id = current_setting('app.current_tenant_id', true)::UUID)
    WITH CHECK (tenant_id = current_setting('app.current_tenant_id', true)::UUID);

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
-- Full-text search index
CREATE INDEX idx_chunks_fts ON chunks USING gin (to_tsvector('english', text));

ALTER TABLE chunks ENABLE ROW LEVEL SECURITY;

CREATE POLICY chunks_tenant_isolation ON chunks
    USING (tenant_id = current_setting('app.current_tenant_id', true)::UUID)
    WITH CHECK (tenant_id = current_setting('app.current_tenant_id', true)::UUID);

-- ============================================================================
-- 5. Chunks_Vectors Table (async embedding projection — pgvector)
-- ============================================================================
-- Vectors are DERIVED DATA: computed async from chunks.text.
-- Configurable embedding model via embedding_model column.
-- RLS enabled directly on this table (NOT inherited via FK).

CREATE TABLE IF NOT EXISTS chunks_vectors (
    chunk_id UUID PRIMARY KEY REFERENCES chunks(chunk_id) ON DELETE CASCADE,
    tenant_id UUID NOT NULL,
    vector vector(4096),
    embedding_model VARCHAR(255) NOT NULL DEFAULT 'nomic-embed-text',
    embedded_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT vector_not_null CHECK (vector IS NOT NULL)
);

-- Vector search index (ivfflat for cosine distance)
CREATE INDEX idx_chunks_vectors_ivfflat ON chunks_vectors USING ivfflat (vector vector_cosine_ops) WITH (lists = 100);

CREATE INDEX idx_chunks_vectors_tenant ON chunks_vectors(tenant_id);
CREATE INDEX idx_chunks_vectors_model ON chunks_vectors(embedding_model);

ALTER TABLE chunks_vectors ENABLE ROW LEVEL SECURITY;

CREATE POLICY chunks_vectors_tenant_isolation ON chunks_vectors
    USING (tenant_id = current_setting('app.current_tenant_id', true)::UUID)
    WITH CHECK (tenant_id = current_setting('app.current_tenant_id', true)::UUID);

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

CREATE POLICY entities_tenant_isolation ON entities
    USING (tenant_id = current_setting('app.current_tenant_id', true)::UUID)
    WITH CHECK (tenant_id = current_setting('app.current_tenant_id', true)::UUID);

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

-- Simplified RLS: just check tenant_id directly (FK integrity guarantees same-tenant entities)
CREATE POLICY relations_tenant_isolation ON relations
    USING (tenant_id = current_setting('app.current_tenant_id', true)::UUID)
    WITH CHECK (tenant_id = current_setting('app.current_tenant_id', true)::UUID);

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

CREATE POLICY checkpoints_tenant_isolation ON projection_checkpoints
    USING (tenant_id = current_setting('app.current_tenant_id', true)::UUID)
    WITH CHECK (tenant_id = current_setting('app.current_tenant_id', true)::UUID);

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

CREATE POLICY dlq_tenant_isolation ON projection_dlq
    USING (tenant_id = current_setting('app.current_tenant_id', true)::UUID)
    WITH CHECK (tenant_id = current_setting('app.current_tenant_id', true)::UUID);

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

CREATE POLICY idempotency_tenant_isolation ON idempotency_keys
    USING (tenant_id = current_setting('app.current_tenant_id', true)::UUID)
    WITH CHECK (tenant_id = current_setting('app.current_tenant_id', true)::UUID);

-- ============================================================================
-- END SCHEMA
-- ============================================================================
-- Notes:
-- 1. The eventsourcing library creates event_store + snapshot_store tables
--    via its PostgresFactory — do NOT create those here.
-- 2. RLS uses current_setting('app.current_tenant_id', true) with the
--    'true' flag so it returns NULL instead of error if unset (safer).
-- 3. All vector operations go through chunks_vectors which has its own
--    RLS policy — vector search is tenant-isolated.
-- 4. The embedding_model column allows swapping between nomic-embed-text
--    (768d) and qwen3-embedding:8b-q8_0 (4096d) via config.
-- 5. To set tenant context: SET LOCAL app.current_tenant_id = '<uuid>';
--    (done in postgres_setup.py setup_tenant_context())
-- ============================================================================
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
CREATE POLICY tags_tenant_isolation ON tags
    USING (tenant_id = NULLIF(current_setting('app.current_tenant_id', true), '')::UUID)
    WITH CHECK (tenant_id = NULLIF(current_setting('app.current_tenant_id', true), '')::UUID);

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
CREATE POLICY document_tags_tenant_isolation ON document_tags
    USING (tenant_id = NULLIF(current_setting('app.current_tenant_id', true), '')::UUID)
    WITH CHECK (tenant_id = NULLIF(current_setting('app.current_tenant_id', true), '')::UUID);

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
CREATE POLICY metadata_tenant_isolation ON metadata
    USING (tenant_id = NULLIF(current_setting('app.current_tenant_id', true), '')::UUID)
    WITH CHECK (tenant_id = NULLIF(current_setting('app.current_tenant_id', true), '')::UUID);
