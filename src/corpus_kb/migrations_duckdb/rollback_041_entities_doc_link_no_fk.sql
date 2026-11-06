-- ============================================================================
-- Rollback migration 041 (DuckDB): rebuild entities WITH the documents FK.
-- ============================================================================
-- Pair of migrations/rollback_040_entities_doc_link_no_fk.sql. The reverse
-- rebuild: entities comes back with
-- source_document_id VARCHAR REFERENCES documents(doc_id) (the plain FK
-- shape 000 kept; DuckDB cannot declare ON DELETE SET NULL). Clean up
-- orphaned source_document_id rows FIRST — a link to a deleted document
-- fails the FK on the INSERT ... SELECT. relations rebuilds too (it
-- references entities, so entities cannot drop first without it).
-- ============================================================================

CREATE TEMP TABLE tmp_entities AS
    SELECT entity_id, tenant_id, name, entity_type, source_document_id,
           metadata, created_at
    FROM entities;

CREATE TEMP TABLE tmp_relations AS
    SELECT relation_id, tenant_id, source_entity_id, target_entity_id,
           relation_type, weight, chunk_id, confidence, extractor_id,
           model_version, prompt_version, metadata, created_at
    FROM relations;

DROP TABLE relations;
DROP TABLE entities;

CREATE TABLE entities (
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

CREATE TABLE relations (
    relation_id VARCHAR PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id VARCHAR NOT NULL,
    source_entity_id VARCHAR NOT NULL REFERENCES entities(entity_id),
    target_entity_id VARCHAR NOT NULL REFERENCES entities(entity_id),
    relation_type VARCHAR(100) NOT NULL DEFAULT 'related_to',
    weight DOUBLE NOT NULL DEFAULT 1.0,
    chunk_id VARCHAR REFERENCES chunks(chunk_id),
    metadata JSON DEFAULT '{}',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

INSERT INTO entities (entity_id, tenant_id, name, entity_type,
                      source_document_id, metadata, created_at)
    SELECT entity_id, tenant_id, name, entity_type,
           source_document_id, metadata, created_at
    FROM tmp_entities;

INSERT INTO relations (relation_id, tenant_id, source_entity_id, target_entity_id,
                       relation_type, weight, chunk_id, confidence, extractor_id,
                       model_version, prompt_version, metadata, created_at)
    SELECT relation_id, tenant_id, source_entity_id, target_entity_id,
           relation_type, weight, chunk_id, confidence, extractor_id,
           model_version, prompt_version, metadata, created_at
    FROM tmp_relations;

DROP TABLE tmp_entities;
DROP TABLE tmp_relations;

CREATE INDEX IF NOT EXISTS idx_entities_tenant ON entities(tenant_id);
CREATE INDEX IF NOT EXISTS idx_entities_type ON entities(entity_type);
CREATE INDEX IF NOT EXISTS idx_entities_tenant_type ON entities(tenant_id, entity_type);
CREATE INDEX IF NOT EXISTS idx_entities_name ON entities(name);
CREATE INDEX IF NOT EXISTS idx_relations_tenant ON relations(tenant_id);
CREATE INDEX IF NOT EXISTS idx_relations_source ON relations(source_entity_id);
CREATE INDEX IF NOT EXISTS idx_relations_target ON relations(target_entity_id);
CREATE INDEX IF NOT EXISTS idx_relations_tenant_source ON relations(tenant_id, source_entity_id);
CREATE INDEX IF NOT EXISTS idx_relations_tenant_target ON relations(tenant_id, target_entity_id);
CREATE UNIQUE INDEX IF NOT EXISTS relations_triple_chunk_uniq
    ON relations (tenant_id, source_entity_id, target_entity_id, relation_type, chunk_id);
CREATE INDEX IF NOT EXISTS idx_relations_chunk ON relations(chunk_id);
