"""U29 LLooM-style concept-induction loop recovery baseline (offline).

Pipeline shape (LLooM): iterate {sample units -> induce concept hypotheses ->
apply concepts to the corpus -> score coverage} until coverage plateaus.
HONESTY NOTE: the real LLooM distills concept hypotheses with an LLM. Here
the hypothesis stage is a PLUGGABLE CALLABLE (:class:`ConceptHypothesizer`);
the CLI always runs the deterministic distinctive-keyword heuristic default —
no live LLM calls are made anywhere. Supply an LLM-backed hypothesizer
programmatically if you want the real thing, and say so in any write-up.

CAVEAT (carried verbatim from the issue): GATOS's validation uses
synthetic planted themes, so results are recovery baselines, not proof of
validity on real interviews. Local-machine baseline on a synthetic fixture
corpus; exits 0 for meaningful runs and honest ``insufficient_data`` reports.
"""

from __future__ import annotations

import argparse
import json
import random
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _inductive_common import (
    LOCAL_MACHINE_LABEL,
    MIN_DOCS,
    RECOVERY_BASELINE_CAVEAT,
    STATUS_OK,
    Document,
    adjusted_rand_index,
    distinctive_keywords,
    insufficient_report,
    load_corpus,
    load_planted_themes,
    markdown_report,
)

DEFAULT_SEED = 20261012
DEFAULT_MAX_ITERATIONS = 8
DEFAULT_SAMPLE_SIZE = 4
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
        best_k, best_j = -1, 0.0
        for k, concept in enumerate(concepts):
            induced = set(concept.terms)
            union = induced | set(planted_keywords)
            jaccard = len(induced & set(planted_keywords)) / len(union) if union else 0.0
            if jaccard > best_j:
                best_k, best_j = k, jaccard
        applied = sum(1 for hit in coverage if best_k in hit) if best_k >= 0 else 0
        per_theme[theme_name] = {
            "best_concept": concepts[best_k].name if best_k >= 0 else None,
            "term_jaccard": round(best_j, 3),
            "documents_covered": applied,
        }
    return per_theme


def run_lloom(
    docs: Sequence[Document],
    themes: dict[str, object],
    seed: int,
    max_iterations: int,
    sample_size: int,
    hypothesizer: ConceptHypothesizer = heuristic_hypothesizer,
) -> dict[str, object]:
    """Meaningful-run report: the sample -> induce -> apply -> score loop."""
    rng = random.Random(seed)
    concepts: list[Concept] = []
    seen_terms: set[frozenset[str]] = set()
    coverage_curve: list[dict[str, object]] = []
    stop_reason = "max_iterations"
    previous_coverage = -1.0
    empty_rounds = 0
    for iteration in range(1, max_iterations + 1):
        covered = {i for i, hit in enumerate(apply_concepts(docs, concepts)) if hit}
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
        covered = {i for i, hit in enumerate(apply_concepts(docs, concepts)) if hit}
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
    membership = apply_concepts(docs, concepts)
    assigned = []
    for hit in membership:
        assigned.append(f"concept_{sorted(hit)[0]}" if hit else "uncovered")
    report: dict[str, object] = {
        "benchmark": "lloom_style_recovery_baseline",
        "status": STATUS_OK,
        "corpus_status": STATUS_OK,
        "document_count": len(docs),
        "seed": seed,
        "loop": {
            "max_iterations": max_iterations,
            "sample_size": sample_size,
            "stop_reason": stop_reason,
            "coverage_curve": coverage_curve,
            "final_coverage": coverage_curve[-1]["coverage"] if coverage_curve else 0.0,
        },
        "concepts": [
            {
                "name": concept.name,
                "terms": list(concept.terms),
                "documents_covered": sum(1 for hit in membership if k in hit),
            }
            for k, concept in enumerate(concepts)
        ],
        "llm_stage": {
            "used": False,
            "note": "concept hypothesis induction is a pluggable "
            "ConceptHypothesizer callable; the CLI default is a deterministic "
            "distinctive-keyword heuristic — no live LLM calls are made",
        },
        "label": LOCAL_MACHINE_LABEL,
        "recovery_baseline_caveat": RECOVERY_BASELINE_CAVEAT,
    }
    doc_labels = themes.get("documents", {})
    if isinstance(doc_labels, dict) and doc_labels and concepts:
        planted = [str(doc_labels.get(doc.name, "")) for doc in docs]
        report["recovery"] = {
            "ari_vs_planted": round(adjusted_rand_index(assigned, planted), 3),
            "ari_note": "docs are coded to their first covering concept; "
            "uncovered docs count as their own group",
            "per_theme": concept_recovery(concepts, membership, docs, themes),
        }
    return report


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    default_input = Path(__file__).resolve().parents[1] / "tests/fixtures/inductive_baselines"
    parser = argparse.ArgumentParser(
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description="LLooM-style concept-induction loop recovery baseline on the "
        "repo fixture corpus (issue #79 / U29).",
        epilog=(
            "The concept-hypothesis stage is a pluggable ConceptHypothesizer "
            "callable; the CLI runs the deterministic heuristic default and makes "
            "NO live LLM calls. Caveat: " + RECOVERY_BASELINE_CAVEAT
        ),
    )
    parser.add_argument("--input", type=Path, default=default_input, help="corpus directory")
    parser.add_argument(
        "--seed", type=int, default=DEFAULT_SEED, help="seed for per-iteration sampling"
    )
    parser.add_argument(
        "--max-iterations", type=int, default=DEFAULT_MAX_ITERATIONS, help="loop iteration cap"
    )
    parser.add_argument(
        "--sample-size",
        type=int,
        default=DEFAULT_SAMPLE_SIZE,
        help="units sampled per iteration",
    )
    parser.add_argument("--format", choices=("json", "markdown"), default="json")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        docs = load_corpus(args.input)
    except (OSError, ValueError) as exc:
        print(json.dumps({"status": "error", "reason": str(exc)}, indent=2))
        return 1
    themes = load_planted_themes(args.input)
    if len(docs) < MIN_DOCS:
        report = insufficient_report("lloom_style_recovery_baseline", len(docs), MIN_DOCS)
    else:
        report = run_lloom(docs, themes, args.seed, args.max_iterations, args.sample_size)
    if args.format == "markdown":
        print(markdown_report("LLooM-style concept induction recovery baseline", report))
    else:
        print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
