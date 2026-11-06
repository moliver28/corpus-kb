-- ============================================================================
-- Rollback migration 040: restore the entities -> documents foreign key.
-- ============================================================================
-- Re-adds the FK the 040 forward migration dropped, with the original
-- ON DELETE SET NULL action. Clean up orphaned source_document_id rows
-- first: a leftover link to a deleted document fails the ADD CONSTRAINT.
-- ============================================================================

ALTER TABLE entities
    ADD CONSTRAINT entities_source_document_id_fkey
    FOREIGN KEY (source_document_id) REFERENCES documents(doc_id)
    ON DELETE SET NULL;
