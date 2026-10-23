-- Rollback migration 033: drop the belief layer.

DROP TABLE IF EXISTS belief_revisions;
DROP TABLE IF EXISTS beliefs;
