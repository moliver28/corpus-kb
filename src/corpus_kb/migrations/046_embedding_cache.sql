-- Persistent embedding cache (issue #539). One row per (sha256, model):
-- NOTE: the issue sketch named this table corpus.embedding_cache, but
-- migration 016 already owns that name (tenant-scoped, dim=1024 CHECK).
-- This cache is tenant-free and fixed 2560-wide, so it gets its own
-- self-describing name.
-- the SHA256 key covers the exact embedded text, including the query-side
-- instruction prefix, so a wording change invalidates every entry on its
-- own. Re-ingest from a cold process reads this table before it calls the
-- embedding model, which removes the re-embed cost of unchanged content.
--
-- No tenant_id and no RLS on purpose: the key is the SHA256 of the text
-- plus the model name, and the vector for the same text is the same for
-- every tenant. Rows hold no tenant-scoped content. Lookup always filters
-- on model, so a model flip never serves a stale vector.
--
-- The vector column is the fixed schema width halfvec(2560) (migration 025
-- regime). Embedders that run at another width skip this cache (RAM only).
--
-- Idempotent: CREATE TABLE IF NOT EXISTS.

CREATE TABLE IF NOT EXISTS embedding_cache_2560 (
    sha256 VARCHAR(64) PRIMARY KEY,
    model TEXT NOT NULL,
    vector halfvec(2560) NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
