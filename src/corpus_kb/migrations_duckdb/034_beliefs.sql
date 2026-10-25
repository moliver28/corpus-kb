-- DuckDB twin of pg migration 033 (beliefs, Living Corpus W4).
-- Translation per the house convention (see 028 header): UUID -> VARCHAR,
-- DOUBLE PRECISION -> DOUBLE, BIGINT identity stays BIGINT.

CREATE TABLE IF NOT EXISTS beliefs (
    belief_id VARCHAR PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id VARCHAR NOT NULL,
    subject TEXT NOT NULL,
    relation TEXT NOT NULL,
    object TEXT NOT NULL,
    alpha DOUBLE NOT NULL,
    beta DOUBLE NOT NULL,
    evidence_count INT NOT NULL DEFAULT 0,
    era_count INT NOT NULL DEFAULT 1,
    prior_strength DOUBLE NOT NULL DEFAULT 2.0,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (tenant_id, subject, relation, object)
);

CREATE SEQUENCE IF NOT EXISTS belief_revisions_revision_id_seq START 1;

CREATE TABLE IF NOT EXISTS belief_revisions (
    revision_id BIGINT PRIMARY KEY DEFAULT nextval('belief_revisions_revision_id_seq'),
    tenant_id VARCHAR NOT NULL,
    belief_id VARCHAR NOT NULL REFERENCES beliefs(belief_id),
    statement_id VARCHAR,
    stance TEXT NOT NULL CHECK (stance IN ('positive','negative')),
    weight DOUBLE NOT NULL,
    observed_at TIMESTAMPTZ NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_beliefs_tenant_subject
    ON beliefs(tenant_id, subject, relation, object);
CREATE INDEX IF NOT EXISTS idx_belief_revisions_belief
    ON belief_revisions(tenant_id, belief_id, observed_at);
