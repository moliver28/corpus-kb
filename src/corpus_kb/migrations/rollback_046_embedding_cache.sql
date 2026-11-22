-- Rollback of migration 046 (embedding_cache_2560, issue #539). The cache is
-- additive: dropping it costs only the re-embed on the next ingest.
DROP TABLE IF EXISTS embedding_cache_2560;
