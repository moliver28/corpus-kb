-- ============================================================================
-- Migration 041 (DuckDB): rebuild entities without the documents FK (issue #330)
-- ============================================================================
-- Pair of migrations/040_entities_doc_link_no_fk.sql. DuckDB has no
-- ALTER TABLE DROP CONSTRAINT, so the FK leaves through a table rebuild.
-- DuckDB 1.5.x turns an UPDATE of a foreign-key column into a delete plus
-- an insert, and relations' FK to entities rejects that insert — so the
-- delete cascade's SET NULL stage on entities.source_document_id needs the
-- FK gone. The gateway-enforced SET NULL action lives in
-- storage.duckdb_cascade.
--
-- relations references entities, so it rebuilds too. The whole file runs in
-- ONE transaction (run_duckdb_migrations wraps every file): a failed
-- rebuild rolls back with no lost rows. Column lists are explicit, and the
-- indexes from 000 and 008 come back at the end. Safe over live rows: the
-- copy-out happens before the drops (verified by
-- tests/test_duckdb_document_delete.py).
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
    -- source_document_id carries NO foreign key: Postgres dropped the FK in
    -- its 040, and the delete cascade applies the SET NULL action in code.
    source_document_id VARCHAR,
    metadata JSON DEFAULT '{}',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (tenant_id, name, entity_type)
);

CREATE TABLE relations (
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
    confidence DOUBLE,
    extractor_id VARCHAR(100),
    model_version VARCHAR(100),
    prompt_version VARCHAR(50),
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

-- Indexes from 000 and 008, verbatim.
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
