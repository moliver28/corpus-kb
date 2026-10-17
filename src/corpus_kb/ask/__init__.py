"""The ask operation (UX program Wave 1): grounded Q&A over the whole KB.

``compose_answer`` orchestrates retrieval, evidence blocks, temperature-0
generation with [n] citations, sentence-level citation filtering, and
optional claim verification — generalizing the notebook pipeline's shape
(``research/notebook.py``) to every document. Failure is an HONEST
envelope, never a raise.
"""

from __future__ import annotations

from corpus_kb.ask.composer import AnswerBundle, compose_answer, evidence_blocks, verify_claims

__all__ = ["AnswerBundle", "compose_answer", "evidence_blocks", "verify_claims"]
