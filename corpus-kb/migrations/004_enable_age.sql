-- Apache AGE graph extension + corpus_kb graph namespace (idempotent).
-- PostgresGraphStore uses cypher() against this graph when graph.backend = "age".
CREATE EXTENSION IF NOT EXISTS age;

-- ag_catalog.create_graph errors when the graph already exists; guard against
-- the ag_catalog.ag_graph catalog so re-applying this migration is a no-op.
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM ag_catalog.ag_graph WHERE name = 'corpus_kb'
    ) THEN
        PERFORM ag_catalog.create_graph('corpus_kb');
    END IF;
END
$$;
