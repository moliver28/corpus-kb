-- Rollback migration 032: drop the statement conflicts.

DROP TABLE IF EXISTS statement_conflicts;
