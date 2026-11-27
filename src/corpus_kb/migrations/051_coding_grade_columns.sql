-- ============================================================================
-- Migration 051: grade + grade_reason on the decision tables (TAO T1, TD-07)
-- ============================================================================
-- One grade column pair on BOTH decision paths (athena G-02):
-- chunk_codes (coder batch path) and research_assignments (deductive run
-- path). final_codes inherits through the coder_cell upsert. ``grade`` is
-- one of A/B/C/U or NULL (pre-T1 rows). ``grade_reason`` is the closed
-- reason list (refine D-10); the CHECK enforces exactly that set. Both
-- columns are NULLable and additive: no data rewrite, no rollback mirror.

ALTER TABLE chunk_codes ADD COLUMN IF NOT EXISTS grade text;
ALTER TABLE chunk_codes ADD COLUMN IF NOT EXISTS grade_reason text
    CONSTRAINT chunk_codes_grade_reason_check
    CHECK (grade_reason IS NULL OR grade_reason IN (
        'insufficient_calibration_seeds', 'signal_not_measured',
        'quote_not_verified', 'reflexive_paradigm', 'eligible_auto_accept',
        'error_bound_above_target', 'conformal_set_not_singleton',
        'escalated_next_rung', 'signals_contradict_decision',
        'structure_canary_failed', 'translation_quality_failed'));

ALTER TABLE final_codes ADD COLUMN IF NOT EXISTS grade text;
ALTER TABLE final_codes ADD COLUMN IF NOT EXISTS grade_reason text
    CONSTRAINT final_codes_grade_reason_check
    CHECK (grade_reason IS NULL OR grade_reason IN (
        'insufficient_calibration_seeds', 'signal_not_measured',
        'quote_not_verified', 'reflexive_paradigm', 'eligible_auto_accept',
        'error_bound_above_target', 'conformal_set_not_singleton',
        'escalated_next_rung', 'signals_contradict_decision',
        'structure_canary_failed', 'translation_quality_failed'));

ALTER TABLE research_assignments ADD COLUMN IF NOT EXISTS grade text;
ALTER TABLE research_assignments ADD COLUMN IF NOT EXISTS grade_reason text
    CONSTRAINT research_assignments_grade_reason_check
    CHECK (grade_reason IS NULL OR grade_reason IN (
        'insufficient_calibration_seeds', 'signal_not_measured',
        'quote_not_verified', 'reflexive_paradigm', 'eligible_auto_accept',
        'error_bound_above_target', 'conformal_set_not_singleton',
        'escalated_next_rung', 'signals_contradict_decision',
        'structure_canary_failed', 'translation_quality_failed'));
