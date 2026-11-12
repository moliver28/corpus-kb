"""U29 LLooM-style concept-induction loop recovery baseline (offline).

Pipeline shape (LLooM): iterate {sample units -> induce concept hypotheses ->
apply concepts to the corpus -> score coverage} until coverage plateaus.
HONESTY NOTE: the real LLooM distills concept hypotheses with an LLM. Here
the hypothesis stage is a PLUGGABLE CALLABLE
(:class:`_lloom_engine.ConceptHypothesizer`); the CLI always runs the
deterministic distinctive-keyword heuristic default — no live LLM calls are
made anywhere. Supply an LLM-backed hypothesizer programmatically if you
want the real thing, and say so in any write-up.

CAVEAT (carried verbatim from the issue): GATOS's validation uses
synthetic planted themes, so results are recovery baselines, not proof of
validity on real interviews. Local-machine baseline on a synthetic fixture
corpus; exits 0 for meaningful runs and honest ``insufficient_data`` reports.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _inductive_common import (
    LOCAL_MACHINE_LABEL,
    MIN_DOCS,
    RECOVERY_BASELINE_CAVEAT,
    STATUS_OK,
    Document,
    insufficient_report,
    load_corpus,
    load_planted_themes,
    markdown_report,
)
from _lloom_engine import (
    Concept,
    ConceptHypothesizer,
    apply_concepts,
    heuristic_hypothesizer,
    recovery_section,
    run_loop,
)

DEFAULT_SEED = 20261012
DEFAULT_MAX_ITERATIONS = 8
DEFAULT_SAMPLE_SIZE = 4


def run_lloom(
    docs: Sequence[Document],
    themes: dict[str, object],
    seed: int,
    max_iterations: int,
    sample_size: int,
    hypothesizer: ConceptHypothesizer = heuristic_hypothesizer,
) -> dict[str, object]:
    """Meaningful-run report: the sample -> induce -> apply -> score loop."""
    concepts, coverage_curve, stop_reason = run_loop(
        docs, seed, max_iterations, sample_size, hypothesizer
    )
    membership = apply_concepts(docs, concepts)
    assigned = [f"concept_{sorted(hit)[0]}" if hit else "uncovered" for hit in membership]
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
        report["recovery"] = recovery_section(docs, themes, concepts, membership, assigned)
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
