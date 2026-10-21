-- ============================================================================
-- Migration 000: base projection schema (DuckDB mirror of storage/schema.sql)
-- ============================================================================
-- storage/schema.sql has no number on Postgres; this numbered mirror gives
-- the DuckDB set the same base tables before the 001..026 pair files run.
--
-- Translation rules (program doc section 4):
--   UUID -> VARCHAR (values stay canonical uuid text; envelopes byte-stable).
--   JSONB -> JSON. halfvec(2560) -> FLOAT[2560]. TIMESTAMPTZ stays.
--   RLS, policies, GIN and HNSW indexes, and CREATE EXTENSION are dropped;
--   the gateway carries tenant predicates, and the search layer owns the
--   fts and vector indexes.
--   DuckDB FKs cannot carry ON DELETE CASCADE / SET NULL. The plain FK is
--   kept (orphan inserts still fail); the gateway enforces the delete
--   action that Postgres delegates to the constraint.
-- ============================================================================

CREATE SCHEMA IF NOT EXISTS corpus;

-- ============================================================================
-- 1. Tenants table
-- ============================================================================

CREATE TABLE IF NOT EXISTS tenants (
    tenant_id VARCHAR PRIMARY KEY DEFAULT gen_random_uuid(),
    name VARCHAR(255) NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Default single-tenant placeholder
INSERT INTO tenants (tenant_id, name)
VALUES ('00000000-0000-0000-0000-000000000001', 'default')
ON CONFLICT DO NOTHING;

-- ============================================================================
-- 2. Documents (projection from DocumentIngested events)
-- ============================================================================

CREATE TABLE IF NOT EXISTS documents (
    doc_id VARCHAR PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id VARCHAR NOT NULL,
    source VARCHAR(1024) NOT NULL,
    source_type VARCHAR(50) NOT NULL DEFAULT 'text',
    chunk_count INT NOT NULL DEFAULT 0,
    file_size BIGINT,
    file_hash VARCHAR(64),
    language VARCHAR(50),
    metadata JSON DEFAULT '{}',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    -- doc_version arrives with migration 016 on Postgres; it is created
    -- here because DuckDB refuses NOT NULL ALTERs on tables that other
    -- tables reference.
    doc_version INT NOT NULL DEFAULT 1,
    UNIQUE (tenant_id, source)
);

CREATE INDEX IF NOT EXISTS idx_documents_tenant ON documents(tenant_id);
CREATE INDEX IF NOT EXISTS idx_documents_source ON documents(source);
CREATE INDEX IF NOT EXISTS idx_documents_hash ON documents(file_hash);

-- ============================================================================
-- 3. Chunks (projection from ChunksAdded events)
-- ============================================================================

CREATE TABLE IF NOT EXISTS chunks (
    chunk_id VARCHAR PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id VARCHAR NOT NULL,
    doc_id VARCHAR NOT NULL REFERENCES documents(doc_id),
    -- Postgres action: ON DELETE CASCADE; the gateway enforces it here.
    chunk_index INT NOT NULL,
    text TEXT NOT NULL,
    source_type VARCHAR(50),
    chunk_type VARCHAR(50),
    entity_name VARCHAR(255),
    heading_path JSON,
    file_path VARCHAR(1024),
    start_line INT,
    end_line INT,
    metadata JSON DEFAULT '{}',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    -- degraded arrives with migration 022 on Postgres; it is created here
    -- because DuckDB refuses NOT NULL ALTERs on tables that other tables
    -- reference.
    degraded BOOLEAN NOT NULL DEFAULT FALSE,
    UNIQUE (tenant_id, doc_id, chunk_index)
);

CREATE INDEX IF NOT EXISTS idx_chunks_tenant ON chunks(tenant_id);
CREATE INDEX IF NOT EXISTS idx_chunks_doc ON chunks(doc_id);
CREATE INDEX IF NOT EXISTS idx_chunks_tenant_doc ON chunks(tenant_id, doc_id);
-- Postgres keeps a GIN index on to_tsvector('english', text). DuckDB uses
-- the fts extension index instead; the FTS manager owns it at runtime.

-- ============================================================================
-- 4. Chunks_vectors (async embedding projection)
-- ============================================================================

CREATE TABLE IF NOT EXISTS chunks_vectors (
    chunk_id VARCHAR PRIMARY KEY REFERENCES chunks(chunk_id),
    -- Postgres action: ON DELETE CASCADE; the gateway enforces it here.
    tenant_id VARCHAR NOT NULL,
    -- halfvec(2560) on Postgres (the migration-025 regime); FLOAT[2560] here.
    embedding FLOAT[2560],
    embedding_model VARCHAR(255) NOT NULL DEFAULT 'qwen3-embedding:4b',
    embedded_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    -- degraded arrives with migration 022 on Postgres; it is created here
    -- because DuckDB refuses NOT NULL and DEFAULT ALTERs on tables that
    -- have dependent entries (references or indexes).
    degraded BOOLEAN NOT NULL DEFAULT FALSE
);

-- Postgres keeps an HNSW index (halfvec_cosine_ops). DuckDB defaults to
-- exact scan; an optional vss HNSW index is runtime-managed, not migrated.
CREATE INDEX IF NOT EXISTS idx_chunks_vectors_tenant ON chunks_vectors(tenant_id);
CREATE INDEX IF NOT EXISTS idx_chunks_vectors_model ON chunks_vectors(embedding_model);

-- ============================================================================
-- 5. Entities (knowledge graph nodes)
-- ============================================================================

CREATE TABLE IF NOT EXISTS entities (
    entity_id VARCHAR PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id VARCHAR NOT NULL,
    name VARCHAR(255) NOT NULL,
    entity_type VARCHAR(100) NOT NULL DEFAULT 'concept',
    source_document_id VARCHAR REFERENCES documents(doc_id),
    -- Postgres action: ON DELETE SET NULL; the gateway enforces it here.
    metadata JSON DEFAULT '{}',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (tenant_id, name, entity_type)
);

CREATE INDEX IF NOT EXISTS idx_entities_tenant ON entities(tenant_id);
CREATE INDEX IF NOT EXISTS idx_entities_type ON entities(entity_type);
CREATE INDEX IF NOT EXISTS idx_entities_tenant_type ON entities(tenant_id, entity_type);
CREATE INDEX IF NOT EXISTS idx_entities_name ON entities(name);

-- ============================================================================
-- 6. Relations (knowledge graph edges)
-- ============================================================================
-- Postgres schema.sql carries the narrow UNIQUE (tenant_id, source, target,
-- relation_type). Migration 008 replaces it with the wider triple+chunk key.
-- DuckDB cannot drop a table constraint, so this mirror creates no narrow
-- key here; 008 adds the end-state unique index.
-- chunk_id arrives with migration 008 on Postgres; it is created here
-- because DuckDB cannot add a foreign-key column by ALTER.

CREATE TABLE IF NOT EXISTS relations (
    relation_id VARCHAR PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id VARCHAR NOT NULL,
    source_entity_id VARCHAR NOT NULL REFERENCES entities(entity_id),
    -- Postgres action: ON DELETE CASCADE; the gateway enforces it here.
    target_entity_id VARCHAR NOT NULL REFERENCES entities(entity_id),
    -- Postgres action: ON DELETE CASCADE; the gateway enforces it here.
    relation_type VARCHAR(100) NOT NULL DEFAULT 'related_to',
    weight DOUBLE NOT NULL DEFAULT 1.0,
    chunk_id VARCHAR REFERENCES chunks(chunk_id),
    -- Added by migration 008; Postgres action: ON DELETE SET NULL.
    metadata JSON DEFAULT '{}',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_relations_tenant ON relations(tenant_id);
CREATE INDEX IF NOT EXISTS idx_relations_source ON relations(source_entity_id);
CREATE INDEX IF NOT EXISTS idx_relations_target ON relations(target_entity_id);
CREATE INDEX IF NOT EXISTS idx_relations_tenant_source ON relations(tenant_id, source_entity_id);
CREATE INDEX IF NOT EXISTS idx_relations_tenant_target ON relations(tenant_id, target_entity_id);

-- ============================================================================
-- 7. Projection checkpoints (catch-up subscription state)
-- ============================================================================

CREATE TABLE IF NOT EXISTS projection_checkpoints (
    projection_name VARCHAR(255) NOT NULL,
    tenant_id VARCHAR NOT NULL,
    last_event_id VARCHAR NOT NULL,
    last_event_timestamp TIMESTAMPTZ NOT NULL,
    checkpoint_timestamp TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (projection_name, tenant_id)
);

CREATE INDEX IF NOT EXISTS idx_checkpoints_tenant ON projection_checkpoints(tenant_id);

-- ============================================================================
-- 8. Projection DLQ (dead-letter queue for failed projections)
-- ============================================================================

CREATE TABLE IF NOT EXISTS projection_dlq (
    dlq_id VARCHAR PRIMARY KEY DEFAULT gen_random_uuid(),
    projection_name VARCHAR(255) NOT NULL,
    tenant_id VARCHAR NOT NULL,
    event_id VARCHAR NOT NULL,
    event_type VARCHAR(255) NOT NULL,
    error_message TEXT NOT NULL,
    error_stacktrace TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    retry_count INT NOT NULL DEFAULT 0,
    resolved BOOLEAN NOT NULL DEFAULT FALSE,
    UNIQUE (projection_name, tenant_id, event_id)
);

CREATE INDEX IF NOT EXISTS idx_dlq_tenant_projection ON projection_dlq(tenant_id, projection_name);
CREATE INDEX IF NOT EXISTS idx_dlq_created ON projection_dlq(created_at DESC);
-- Postgres keeps this index partial (WHERE resolved = FALSE); DuckDB gets
-- the same columns without the predicate.
CREATE INDEX IF NOT EXISTS idx_dlq_unresolved ON projection_dlq(tenant_id, resolved);

-- ============================================================================
-- 9. Idempotency keys (command deduplication)
-- ============================================================================

CREATE TABLE IF NOT EXISTS idempotency_keys (
    idempotency_key VARCHAR(255) NOT NULL,
    tenant_id VARCHAR NOT NULL,
    command_type VARCHAR(255) NOT NULL,
    command_payload JSON NOT NULL,
    result JSON,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    expires_at TIMESTAMPTZ NOT NULL DEFAULT now() + INTERVAL '24 hours',
    PRIMARY KEY (idempotency_key, tenant_id)
);

CREATE INDEX IF NOT EXISTS idx_idempotency_expires ON idempotency_keys(expires_at);
CREATE INDEX IF NOT EXISTS idx_idempotency_tenant ON idempotency_keys(tenant_id);

-- ============================================================================
-- 10. Tags and document tags
-- ============================================================================

CREATE TABLE IF NOT EXISTS tags (
    tag_id VARCHAR PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id VARCHAR NOT NULL,
    name VARCHAR(255) NOT NULL,
    color VARCHAR(50),
    description TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (tenant_id, name)
);

CREATE INDEX IF NOT EXISTS idx_tags_tenant ON tags(tenant_id);
CREATE INDEX IF NOT EXISTS idx_tags_name ON tags(name);

CREATE TABLE IF NOT EXISTS document_tags (
    doc_id VARCHAR NOT NULL REFERENCES documents(doc_id),
    -- Postgres action: ON DELETE CASCADE; the gateway enforces it here.
    tenant_id VARCHAR NOT NULL,
    tag_id VARCHAR NOT NULL REFERENCES tags(tag_id),
    -- Postgres action: ON DELETE CASCADE; the gateway enforces it here.
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (doc_id, tag_id)
);

CREATE INDEX IF NOT EXISTS idx_document_tags_tenant ON document_tags(tenant_id);
CREATE INDEX IF NOT EXISTS idx_document_tags_doc ON document_tags(doc_id);

-- ============================================================================
-- 11. Metadata (key-value store)
-- ============================================================================

CREATE TABLE IF NOT EXISTS metadata (
    key VARCHAR(255) NOT NULL,
    value TEXT,
    doc_id VARCHAR REFERENCES documents(doc_id),
    -- Postgres action: ON DELETE CASCADE; the gateway enforces it here.
    tenant_id VARCHAR NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (key, tenant_id, doc_id)
);

CREATE INDEX IF NOT EXISTS idx_metadata_tenant ON metadata(tenant_id);
CREATE INDEX IF NOT EXISTS idx_metadata_key ON metadata(key);
