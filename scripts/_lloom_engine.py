"""LLooM-style loop engine: concepts, hypothesizer, loop, recovery scoring.

The deterministic engine behind scripts/bench_lloom_baseline.py. The
hypothesis stage is PLUGGABLE (:class:`ConceptHypothesizer`); the default
is a distinctive-keyword heuristic — no live LLM calls anywhere.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol

from corpus_kb.utils.deterministic import HashRandom

from _inductive_common import Document, adjusted_rand_index, distinctive_keywords

CONCEPT_TERMS = 2
COVERAGE_PLATEAU = 0.01


class ConceptHypothesizer(Protocol):
    """Pluggable hypothesis stage (an LLM-backed one would go here)."""

    def __call__(self, sample: Sequence[Document], corpus: Sequence[Document]) -> list[Concept]: ...


@dataclass(frozen=True)
class Concept:
    """One induced concept hypothesis: a name and its membership terms."""

    name: str
    terms: tuple[str, ...]


def heuristic_hypothesizer(sample: Sequence[Document], corpus: Sequence[Document]) -> list[Concept]:
    """Deterministic default: keywords grounded in multiple sampled units.

    This stands in for LLooM's LLM distillation step. It is a keyword
    heuristic, NOT a language model. Candidate terms must appear in at least
    two sampled units (a concept is grounded in multiple units); when no term
    qualifies, plain distinctiveness ranking applies.
    """
    keywords = distinctive_keywords(sample, corpus, CONCEPT_TERMS, min_member_docs=2)
    if len(keywords) < CONCEPT_TERMS:
        return []
    return [Concept(name="+".join(keywords), terms=tuple(keywords))]


def apply_concepts(docs: Sequence[Document], concepts: Sequence[Concept]) -> list[set[int]]:
    """Membership: a document is covered by a concept when it has any term."""
    membership: list[set[int]] = []
    for doc in docs:
        hit = {
            k
            for k, concept in enumerate(concepts)
            if any(term in doc.tokens for term in concept.terms)
        }
        membership.append(hit)
    return membership


def _covered_docs(docs: Sequence[Document], concepts: Sequence[Concept]) -> set[int]:
    """Indexes of documents covered by at least one concept."""
    return {i for i, hit in enumerate(apply_concepts(docs, concepts)) if hit}


def run_loop(
    docs: Sequence[Document],
    seed: int,
    max_iterations: int,
    sample_size: int,
    hypothesizer: ConceptHypothesizer = heuristic_hypothesizer,
) -> tuple[list[Concept], list[dict[str, object]], str]:
    """Sample -> induce -> apply -> score until a stop condition holds.

    Returns (concepts, coverage_curve, stop_reason). Stop reasons:
    ``max_iterations``, ``no_new_hypotheses`` (two empty rounds), and
    ``coverage_plateau`` (coverage moved less than COVERAGE_PLATEAU).
    """
    rng = HashRandom(seed)
    concepts: list[Concept] = []
    seen_terms: set[frozenset[str]] = set()
    coverage_curve: list[dict[str, object]] = []
    stop_reason = "max_iterations"
    previous_coverage = -1.0
    empty_rounds = 0
    for iteration in range(1, max_iterations + 1):
        covered = _covered_docs(docs, concepts)
        pool = sorted(set(range(len(docs))) - covered) or list(range(len(docs)))
        pick = sorted(rng.sample(pool, min(sample_size, len(pool))))
        candidates = hypothesizer([docs[i] for i in pick], docs)
        fresh = [c for c in candidates if frozenset(c.terms) not in seen_terms]
        if not fresh:
            empty_rounds += 1
            if empty_rounds >= 2:
                stop_reason = "no_new_hypotheses"
                break
            continue
        empty_rounds = 0
        concepts.extend(fresh)
        seen_terms.update(frozenset(c.terms) for c in fresh)
        covered = _covered_docs(docs, concepts)
        fraction = len(covered) / len(docs)
        coverage_curve.append(
            {
                "iteration": iteration,
                "coverage": round(fraction, 3),
                "n_concepts": len(concepts),
            }
        )
        if fraction - previous_coverage < COVERAGE_PLATEAU and iteration > 1:
            stop_reason = "coverage_plateau"
            break
        previous_coverage = fraction
    return concepts, coverage_curve, stop_reason


def concept_recovery(
    concepts: Sequence[Concept],
    coverage: Sequence[set[int]],
    docs: Sequence[Document],
    themes: dict[str, object],
) -> dict[str, object]:
    """Score induced concepts against planted themes (Jaccard + best-match)."""
    doc_labels = themes.get("documents", {})
    theme_defs = themes.get("themes", {})
    assert isinstance(doc_labels, dict) and isinstance(theme_defs, dict)
    per_theme: dict[str, object] = {}
    for theme_name, theme_def in theme_defs.items():
        assert isinstance(theme_def, dict)
        planted_keywords = theme_def.get("keywords", [])
        assert isinstance(planted_keywords, list)
        best_k, best_j = _best_theme_concept(concepts, planted_keywords)
        applied = sum(1 for hit in coverage if best_k in hit) if best_k >= 0 else 0
        per_theme[theme_name] = {
            "best_concept": concepts[best_k].name if best_k >= 0 else None,
            "term_jaccard": round(best_j, 3),
            "documents_covered": applied,
        }
    return per_theme


def _best_theme_concept(
    concepts: Sequence[Concept], planted_keywords: list[object]
) -> tuple[int, float]:
    """(best index, Jaccard) of the concept closest to one planted theme."""
    planted = set(planted_keywords)
    best_k, best_j = -1, 0.0
    for k, concept in enumerate(concepts):
        induced = set(concept.terms)
        union = induced | planted
        jaccard = len(induced & planted) / len(union) if union else 0.0
        if jaccard > best_j:
            best_k, best_j = k, jaccard
    return best_k, best_j


def recovery_section(
    docs: Sequence[Document],
    themes: dict[str, object],
    concepts: Sequence[Concept],
    membership: Sequence[set[int]],
    assigned: Sequence[str],
) -> dict[str, object]:
    """ARI against the planted doc labels plus per-theme scoring."""
    doc_labels = themes.get("documents", {})
    assert isinstance(doc_labels, dict)
    planted = [str(doc_labels.get(doc.name, "")) for doc in docs]
    return {
        "ari_vs_planted": round(adjusted_rand_index(assigned, planted), 3),
        "ari_note": "docs are coded to their first covering concept; "
        "uncovered docs count as their own group",
        "per_theme": concept_recovery(concepts, membership, docs, themes),
    }
