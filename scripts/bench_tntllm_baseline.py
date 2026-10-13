"""U29 TnT-LLM-style taxonomy-generation-then-classification baseline (offline).

Pipeline shape (TnT-LLM): generate a code taxonomy from a small seeded sample
of the corpus, then classify the remaining documents against that taxonomy.
HONESTY NOTE: the real TnT-LLM pipeline uses an LLM for both stages. Here the
taxonomy-induction stage is a PLUGGABLE CALLABLE (:class:`TaxonomyInducer`);
the CLI always runs the deterministic keyword/clustering heuristic default —
no live LLM calls are made anywhere. Supply an LLM-backed inducer
programmatically if you want the real thing, and say so in any write-up.

CAVEAT (carried verbatim from the issue): GATOS-style validation uses
synthetic planted themes, so results are recovery baselines, NOT proof of
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
    cosine,
    distinctive_keywords,
    greedy_agglomerative,
    insufficient_report,
    load_corpus,
    load_planted_themes,
    markdown_report,
    tfidf_vectors,
)

DEFAULT_SEED = 20261012
TAXONOMY_THRESHOLD = 0.05
SAMPLE_FRAC = 0.5
MAX_CODE_TERMS = 5


class TaxonomyInducer(Protocol):
    """Pluggable taxonomy-generation stage (an LLM-backed one would go here)."""

    def __call__(self, sample: Sequence[Document]) -> list[CodeProposal]: ...


@dataclass(frozen=True)
class CodeProposal:
    """One induced taxonomy code: name, definition, matching terms, members."""

    name: str
    definition: str
    terms: tuple[str, ...]
    member_doc_ids: tuple[str, ...]


def heuristic_taxonomy_inducer(sample: Sequence[Document]) -> list[CodeProposal]:
    """Deterministic default: cluster the sample, name clusters by keywords.

    This stands in for TnT-LLM's LLM taxonomy-generation stage. It is a
    clustering + keyword heuristic, NOT a language model.
    """
    vectors = tfidf_vectors(sample)
    assignment = greedy_agglomerative(vectors, TAXONOMY_THRESHOLD)
    members_by_cluster: dict[int, list[int]] = {}
    for idx, cluster in enumerate(assignment):
        members_by_cluster.setdefault(cluster, []).append(idx)
    codes: list[CodeProposal] = []
    for members in members_by_cluster.values():
        member_docs = [sample[i] for i in members]
        terms = tuple(distinctive_keywords(member_docs, sample, MAX_CODE_TERMS))
        name = "-".join(terms[:2]) if terms else "unlabelled-code"
        definition = f"{len(members)} sample documents; terms {', '.join(terms) or 'n/a'}"
        codes.append(
            CodeProposal(
                name=name,
                definition=definition,
                terms=terms,
                member_doc_ids=tuple(sample[i].doc_id for i in members),
            )
        )
    return codes


def code_centroids(
    docs: Sequence[Document],
    vectors: Sequence[dict[str, float]],
    codes: Sequence[CodeProposal],
) -> list[dict[str, float]]:
    """L2-normalized mean TF-IDF vector per taxonomy code."""
    doc_index = {doc.doc_id: i for i, doc in enumerate(docs)}
    centroids: list[dict[str, float]] = []
    for code in codes:
        total: dict[str, float] = {}
        for did in code.member_doc_ids:
            for term, weight in vectors[doc_index[did]].items():
                total[term] = total.get(term, 0.0) + weight
        norm = sum(w * w for w in total.values()) ** 0.5 or 1.0
        centroids.append({term: weight / norm for term, weight in total.items()})
    return centroids


def classify_docs(
    vectors: Sequence[dict[str, float]], centroids: Sequence[dict[str, float]]
) -> list[int]:
    """Nearest-centroid classification (argmax cosine, ties -> lowest index)."""
    picks: list[int] = []
    for vec in vectors:
        scores = [cosine(vec, centroid) for centroid in centroids]
        picks.append(max(range(len(scores)), key=lambda i: (scores[i], -i)))
    return picks


def _majority_planted_code(
    theme: str, codes: Sequence[CodeProposal], planted_by_doc_id: dict[str, str]
) -> int:
    """Index of the code whose sample members are majority-planted ``theme``."""
    best_code, best_count = 0, -1
    for k, code in enumerate(codes):
        count = sum(1 for did in code.member_doc_ids if planted_by_doc_id.get(did, "") == theme)
        if count > best_count:
            best_code, best_count = k, count
    return best_code


def run_tntllm(
    docs: Sequence[Document],
    themes: dict[str, object],
    seed: int,
    sample_frac: float,
    inducer: TaxonomyInducer = heuristic_taxonomy_inducer,
) -> dict[str, object]:
    """Meaningful-run report: induce taxonomy on the sample, classify the rest."""
    order = list(range(len(docs)))
    random.Random(seed).shuffle(order)
    n_sample = max(2, round(sample_frac * len(docs)))
    sample_idx, held_out_idx = order[:n_sample], order[n_sample:]
    sample_docs = [docs[i] for i in sample_idx]
    codes = inducer(sample_docs)
    vectors = tfidf_vectors(docs)
    centroids = code_centroids(docs, vectors, codes)
    held_out_picks = classify_docs([vectors[i] for i in held_out_idx], centroids)
    code_of_sample = {did: k for k, code in enumerate(codes) for did in code.member_doc_ids}
    assigned = [""] * len(docs)
    for i in sample_idx:
        assigned[i] = f"code_{code_of_sample[docs[i].doc_id]}"
    for k, i in enumerate(held_out_idx):
        assigned[i] = f"code_{held_out_picks[k]}"
    report: dict[str, object] = {
        "benchmark": "tntllm_style_recovery_baseline",
        "status": STATUS_OK,
        "corpus_status": STATUS_OK,
        "document_count": len(docs),
        "seed": seed,
        "sample_size": len(sample_docs),
        "held_out_size": len(held_out_idx),
        "taxonomy_size": len(codes),
        "taxonomy": [
            {"name": code.name, "definition": code.definition, "terms": list(code.terms)}
            for code in codes
        ],
        "llm_stage": {
            "used": False,
            "note": "taxonomy induction is a pluggable TaxonomyInducer callable; "
            "the CLI default is a deterministic clustering/keyword heuristic — "
            "no live LLM calls are made",
        },
        "label": LOCAL_MACHINE_LABEL,
        "recovery_baseline_caveat": RECOVERY_BASELINE_CAVEAT,
    }
    doc_labels = themes.get("documents", {})
    theme_defs = themes.get("themes", {})
    if isinstance(doc_labels, dict) and doc_labels and isinstance(theme_defs, dict):
        planted_by_doc_id = {doc.doc_id: str(doc_labels.get(doc.name, "")) for doc in docs}
        planted = [planted_by_doc_id[doc.doc_id] for doc in docs]
        theme_names = set(theme_defs)
        pairs = [(assigned[i], planted[i]) for i in held_out_idx if planted[i] in theme_names]
        hits = sum(
            1
            for got, want in pairs
            if got == f"code_{_majority_planted_code(want, codes, planted_by_doc_id)}"
        )
        report["recovery"] = {
            "ari_vs_planted": round(adjusted_rand_index(assigned, planted), 3),
            "held_out_accuracy_pure_theme": (round(hits / len(pairs), 3) if pairs else None),
            "n_pure_theme_docs_held_out": len(pairs),
            "accuracy_note": "accuracy over held-out pure-theme docs only; a hit "
            "means the doc was assigned the code whose sample members are "
            "majority-planted with the doc's theme",
        }
    return report


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    default_input = Path(__file__).resolve().parents[1] / "tests/fixtures/inductive_baselines"
    parser = argparse.ArgumentParser(
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description="TnT-LLM-style taxonomy-generation-then-classification recovery "
        "baseline on the repo fixture corpus (issue #79 / U29).",
        epilog=(
            "The taxonomy-generation stage is a pluggable TaxonomyInducer callable; "
            "the CLI runs the deterministic heuristic default and makes NO live LLM "
            "calls. Caveat: " + RECOVERY_BASELINE_CAVEAT
        ),
    )
    parser.add_argument("--input", type=Path, default=default_input, help="corpus directory")
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED, help="seed for the sample split")
    parser.add_argument(
        "--sample-frac",
        type=float,
        default=SAMPLE_FRAC,
        help="fraction of docs used for taxonomy induction",
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
        report = insufficient_report("tntllm_style_recovery_baseline", len(docs), MIN_DOCS)
    else:
        report = run_tntllm(docs, themes, args.seed, args.sample_frac)
    if args.format == "markdown":
        print(markdown_report("TnT-LLM-style taxonomy-then-classify recovery baseline", report))
    else:
        print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
