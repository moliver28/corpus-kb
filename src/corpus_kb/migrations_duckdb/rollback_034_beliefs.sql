-- Rollback (DuckDB) for 034: drop the belief layer twin.

DROP TABLE IF EXISTS belief_revisions;
DROP TABLE IF EXISTS beliefs;
DROP SEQUENCE IF EXISTS belief_revisions_revision_id_seq;
