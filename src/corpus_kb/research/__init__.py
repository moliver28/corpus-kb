"""Qualitative-research subsystem for Corpus-KB (v5).

Everything the research JTBDs run on lives here: transcript ingestion
(parsers, role heuristics, exchange linking, dynamic-ingest ledger — txt
first-class, vtt/srt/csv/docx via the same turn-recovery approach), the
inductive and deductive coding engines, calibration and governance reports,
the review surface, the notebook with exchange-level citations, the narrated
demo, and the research cycle orchestrator. User-facing prose is centralized
in ``guide_copy``; formulas and their provenance live in
docs/research-math.md. No audio/video, no new parser libraries (guardrail).
"""
