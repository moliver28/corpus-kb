CREATE SCHEMA IF NOT EXISTS corpus;

CREATE TABLE IF NOT EXISTS corpus.sources (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    repo_path TEXT NOT NULL,
    file_path TEXT NOT NULL,
    language TEXT,
    git_rev TEXT,
    content_hash TEXT NOT NULL,
    ingested_at TIMESTAMPTZ DEFAULT now(),
    UNIQUE (repo_path, file_path, git_rev)
);

CREATE TABLE IF NOT EXISTS corpus.nodes (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    source_id UUID REFERENCES corpus.sources(id) ON DELETE CASCADE,
    chunk_index INT NOT NULL,
    text TEXT NOT NULL,
    metadata JSONB DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS corpus.index_generations (
    id SERIAL PRIMARY KEY,
    embed_model TEXT NOT NULL,
    embed_dim INT NOT NULL,
    table_name TEXT NOT NULL UNIQUE,
    active BOOLEAN DEFAULT FALSE,
    created_at TIMESTAMPTZ DEFAULT now()
);
