-- DuckDB twin of pg migration 031 (statement atoms, Living Corpus W2).
-- Translation per the house convention (see 028 header): UUID -> VARCHAR,
-- jsonb -> JSON, FLOAT -> DOUBLE. No RLS: the DuckDB gateway enforces the
-- tenant predicate per statement.

CREATE TABLE IF NOT EXISTS statements (
    statement_id VARCHAR PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id VARCHAR NOT NULL,
    chunk_id VARCHAR NOT NULL REFERENCES chunks(chunk_id),
    document_id VARCHAR NOT NULL REFERENCES documents(doc_id),
    statement_hash CHAR(64) NOT NULL,
    text TEXT NOT NULL,
    template_id TEXT NOT NULL,
    template_version TEXT NOT NULL,
    start_char INT,
    end_char INT,
    entities JSON NOT NULL,
    exchange_id BIGINT,
    valid_at TIMESTAMPTZ,
    observed_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    confidence DOUBLE NOT NULL DEFAULT 0.5,
    superseded_by VARCHAR,
    metadata JSON NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (tenant_id, statement_hash, chunk_id)
);

CREATE INDEX IF NOT EXISTS idx_statements_tenant_chunk
    ON statements(tenant_id, chunk_id);
CREATE INDEX IF NOT EXISTS idx_statements_tenant_valid
    ON statements(tenant_id, valid_at NULLS LAST, observed_at, statement_id);
CREATE INDEX IF NOT EXISTS idx_statements_tenant_template
    ON statements(tenant_id, template_id);
