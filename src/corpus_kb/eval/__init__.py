"""Offline evaluation harness (UX program Wave 5).

Golden-set JSONL files plus retrieval and answer-quality metrics, run
fully local. A lifecycle command family (``corpus-kb eval ...``) — NOT a
catalogue operation, so ``surface_registry``, ``contracts/`` and the
parity gates are untouched (the ``cli_quickstart`` precedent).

Design contract:

- METRIC REUSE: recall@k, MRR and nDCG@10 come from
  ``research/retrieval_eval.py`` (the U45 harness). The retrieval path
  ranks through ``research.retrieval.ranked_children`` — the same seam
  the live search and the calibration sweeps use — so the numbers are
  the same species the research benchmarks report.
- LAZY DEPENDENCY: deepeval is imported ONLY inside
  ``eval/deepeval_adapter.py``, behind the ``topics/engine.py`` import
  seam. A missing ``[eval]`` extra is an honest ``skipped`` section in
  the report (or a typed ``EvalDependencyError`` at the adapter), never
  a crash and never a fabricated score.
- NO CLOUD CALLS: the judge is a local Ollama model at temperature 0.
  Nothing here talks to an API provider.
"""

from corpus_kb.eval.golden import (
    GoldenFormatError,
    GoldenRecord,
    build_golden_from_assignments,
    golden_summary,
    load_golden,
)
from corpus_kb.eval.report import EvalReport, RetrievalRow, RetrievalSummary, to_envelope

__all__ = [
    "EvalReport",
    "GoldenFormatError",
    "GoldenRecord",
    "RetrievalRow",
    "RetrievalSummary",
    "build_golden_from_assignments",
    "golden_summary",
    "load_golden",
    "to_envelope",
]
