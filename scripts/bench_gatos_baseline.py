"""U29 GATOS-style inductive-coding recovery baseline (offline, deterministic).

Pipeline shape: greedy agglomerative clustering of the fixture corpus, then a
topic-model-style pass that names each cluster with its most distinctive
keywords (the induced "codes"). Recovery is scored against the planted themes
in the fixture labels: adjusted Rand index, per-theme precision/recall with
keyword Jaccard, plus a post-hoc threshold sweep.

CAVEAT (carried verbatim from the issue): GATOS-style validation uses
synthetic planted themes, so results are recovery baselines, NOT proof of
validity on real interviews. Everything here is a local-machine baseline on a
synthetic fixture corpus; no LLM calls, no network, no database. Greedy
agglomerative merging is fully deterministic, so ``--seed`` is accepted for
interface parity with the other U29 baselines and recorded in the report.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _inductive_common import (
    LOCAL_MACHINE_LABEL,
    MIN_DOCS,
    RECOVERY_BASELINE_CAVEAT,
    STATUS_OK,
    Document,
    adjusted_rand_index,
    distinctive_keywords,
    greedy_agglomerative,
    insufficient_report,
    load_corpus,
    load_planted_themes,
    markdown_report,
    tfidf_vectors,
)

DEFAULT_SEED = 20261012
DEFAULT_THRESHOLD = 0.05
SWEEP_START, SWEEP_STOP, SWEEP_STEP = 0.01, 0.30, 0.01


@dataclass(frozen=True)
class SweepPoint:
    """One row of the post-hoc threshold sweep (serialized verbatim to JSON)."""

    threshold: float
    ari: float
    n_clusters: int


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    default_input = Path(__file__).resolve().parents[1] / "tests/fixtures/inductive_baselines"
    parser = argparse.ArgumentParser(
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description="GATOS-style greedy agglomerative + topic-model inductive coding "
        "recovery baseline on the repo fixture corpus (issue #79 / U29).",
        epilog=(
            "Caveat: "
            + RECOVERY_BASELINE_CAVEAT
            + " Results are a "
            + LOCAL_MACHINE_LABEL
            + ". Exits 0 for both meaningful runs and honest "
            "'insufficient_data' reports."
        ),
    )
    parser.add_argument("--input", type=Path, default=default_input, help="corpus directory")
    parser.add_argument(
        "--seed", type=int, default=DEFAULT_SEED, help="recorded for parity; run is deterministic"
    )
    parser.add_argument(
        "--threshold",
        type=float,
        default=DEFAULT_THRESHOLD,
        help="merge-similarity threshold for the primary run",
    )
    parser.add_argument("--max-keywords", type=int, default=5, help="keywords per induced code")
    parser.add_argument("--format", choices=("json", "markdown"), default="json")
    return parser.parse_args(argv)


def canonical_cluster_labels(assignment: Sequence[int]) -> list[str]:
    """Relabel clusters as cluster_00.. in order of first appearance."""
    remap: dict[int, str] = {}
    labels: list[str] = []
    for raw in assignment:
        if raw not in remap:
            remap[raw] = f"cluster_{len(remap):02d}"
        labels.append(remap[raw])
    return labels


def theme_recovery(
    cluster_labels: Sequence[str],
    doc_names: Sequence[str],
    themes: dict[str, object],
    keywords_by_cluster: dict[str, Sequence[str]],
) -> dict[str, object]:
    """Per-planted-theme best-cluster recovery; {} when no labels exist."""
    doc_labels = themes.get("documents", {})
    theme_defs = themes.get("themes", {})
    assert isinstance(doc_labels, dict) and isinstance(theme_defs, dict)
    per_theme: dict[str, object] = {}
    cluster_ids = sorted(set(cluster_labels))
    for theme_name in theme_defs:
        theme_docs = [i for i, name in enumerate(doc_names) if doc_labels.get(name) == theme_name]
        if not theme_docs:
            continue
        best_cluster, best_overlap = cluster_ids[0], 0
        for cid in cluster_ids:
            overlap = sum(1 for i in theme_docs if cluster_labels[i] == cid)
            if overlap > best_overlap:
                best_cluster, best_overlap = cid, overlap
        cluster_size = sum(1 for label in cluster_labels if label == best_cluster)
        recall = best_overlap / len(theme_docs)
        precision = best_overlap / cluster_size if cluster_size else 0.0
        f1 = 2 * precision * recall / (precision + recall) if (precision + recall) else 0.0
        theme_def = theme_defs[theme_name]
        assert isinstance(theme_def, dict)
        planted_keywords = theme_def.get("keywords", [])
        assert isinstance(planted_keywords, list)
        induced = set(keywords_by_cluster.get(best_cluster, []))
        union = induced | set(planted_keywords)
        jaccard = len(induced & set(planted_keywords)) / len(union) if union else 0.0
        per_theme[theme_name] = {
            "best_cluster": best_cluster,
            "recovered": best_overlap,
            "theme_size": len(theme_docs),
            "precision": round(precision, 3),
            "recall": round(recall, 3),
            "f1": round(f1, 3),
            "keyword_jaccard": round(jaccard, 3),
        }
    return per_theme


def run_gatos(
    docs: Sequence[Document],
    themes: dict[str, object],
    seed: int,
    threshold: float,
    max_keywords: int,
) -> dict[str, object]:
    """Meaningful-run report: cluster, name the codes, score recovery vs plants."""
    vectors = tfidf_vectors(docs)
    assignment = greedy_agglomerative(vectors, threshold)
    cluster_labels = canonical_cluster_labels(assignment)
    cluster_ids = sorted(set(cluster_labels))
    keywords_by_cluster: dict[str, Sequence[str]] = {}
    induced_codes: list[dict[str, object]] = []
    for cid in cluster_ids:
        members = [docs[i] for i, label in enumerate(cluster_labels) if label == cid]
        keywords = distinctive_keywords(members, docs, max_keywords)
        keywords_by_cluster[cid] = keywords
        induced_codes.append({"code": cid, "size": len(members), "keywords": keywords})
    report: dict[str, object] = {
        "benchmark": "gatos_style_recovery_baseline",
        "status": STATUS_OK,
        "corpus_status": STATUS_OK,
        "document_count": len(docs),
        "seed": seed,
        "seed_note": "run is fully deterministic; seed recorded for parity",
        "threshold": threshold,
        "n_clusters": len(cluster_ids),
        "induced_codes": induced_codes,
        "label": LOCAL_MACHINE_LABEL,
        "recovery_baseline_caveat": RECOVERY_BASELINE_CAVEAT,
    }
    doc_labels = themes.get("documents", {})
    if isinstance(doc_labels, dict) and doc_labels:
        planted_labels = [str(doc_labels.get(doc.name, "")) for doc in docs]
        doc_names = [doc.name for doc in docs]
        report["recovery"] = {
            "ari_vs_planted": round(adjusted_rand_index(cluster_labels, planted_labels), 3),
            "per_theme": theme_recovery(cluster_labels, doc_names, themes, keywords_by_cluster),
        }
        sweep: list[SweepPoint] = []
        t = SWEEP_START
        while t <= SWEEP_STOP + 1e-9:
            sweep_labels = canonical_cluster_labels(greedy_agglomerative(vectors, t))
            sweep.append(
                SweepPoint(
                    threshold=round(t, 2),
                    ari=round(adjusted_rand_index(sweep_labels, planted_labels), 3),
                    n_clusters=len(set(sweep_labels)),
                )
            )
            t += SWEEP_STEP
        best = max(sweep, key=lambda point: (point.ari, -point.threshold))
        report["threshold_sweep"] = [asdict(point) for point in sweep]
        report["oracle_note"] = (
            "sweep best is an optimistic oracle bound (threshold chosen post hoc); "
            "the primary --threshold run above is the honest operating point"
        )
        report["oracle_best"] = asdict(best)
    return report


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        docs = load_corpus(args.input)
    except (OSError, ValueError) as exc:
        print(json.dumps({"status": "error", "reason": str(exc)}, indent=2))
        return 1
    themes = load_planted_themes(args.input)
    if len(docs) < MIN_DOCS:
        report = insufficient_report("gatos_style_recovery_baseline", len(docs), MIN_DOCS)
    else:
        report = run_gatos(docs, themes, args.seed, args.threshold, args.max_keywords)
    if args.format == "markdown":
        print(markdown_report("GATOS-style inductive coding recovery baseline", report))
    else:
        print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
