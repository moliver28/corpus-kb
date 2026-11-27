"""MECE math over fetched coding state (TD-11, R9b) — pure, no I/O.

Composes the repo's existing math instead of recomputing it:
``research/overlap.code_centroid_matrix``, ``research/exhaustiveness``
(residuals + residual_section), and ``research/cluster_stability``
(spherical_kmeans, bootstrap_stability, silhouette_by_group). The embedder
name rides every report (S9). Every share carries its n and a Wilson
95 percent interval (athena AM-02). Inputs come from stored vectors only,
so no model call runs here (wave law). The DB reads live in
:mod:`corpus_kb.coding.report_reads`.
"""

from __future__ import annotations

import json
import math
from collections import Counter
from typing import Any

from corpus_kb.coding.residue_terms import residue_top_terms
from corpus_kb.research.cluster_stability import (
    bootstrap_stability,
    silhouette_by_group,
    spherical_kmeans,
)
from corpus_kb.research.exhaustiveness import residual_section, residuals
from corpus_kb.research.overlap import code_centroid_matrix

__all__ = ["mece_block", "wilson_interval"]

#: No-gold fallback for tau_res (calibrate_tau_res floors at the same value).
TAU_RES_FLOOR = 0.05

#: Deterministic cap on chunk vectors the report reads (ordered by chunk_id).
MAX_CHUNKS = 5000


def wilson_interval(hits: int, n: int, z: float = 1.96) -> list[float]:
    """Wilson 95 percent interval around one proportion (stdlib math)."""
    if n == 0:
        return [0.0, 0.0]
    p = hits / n
    denom = 1 + z * z / n
    center = (p + z * z / (2 * n)) / denom
    spread = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return [max(0.0, center - spread), min(1.0, center + spread)]


def share(hits: int, n: int) -> dict[str, object]:
    """One reported share: value, n, and its Wilson interval."""
    return {"value": (hits / n) if n else 0.0, "n": n, "interval": wilson_interval(hits, n)}


def parse_vector(raw: object) -> list[float] | None:
    """Stored vector -> floats; None when absent, unparsable, or all zeros.

    Three storage shapes arrive: a pgvector literal string (asyncpg), a list
    (JSON), and a tuple (the DuckDB gateway's FLOAT[k] columns).
    """
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except ValueError:
            return None
    if not isinstance(raw, (list, tuple)) or not raw or not any(raw):
        return None
    return [float(x) for x in raw]


def _exclusivity_block(
    sampled: list[str],
    vectors: dict[str, list[float]],
    coded_by_chunk: dict[str, set[str]],
    keyword_codes: dict[str, set[str]],
    code_ids: list[str],
    centroids: dict[str, list[float]],
) -> dict[str, object]:
    """Exclusivity: multi-code shares, centroid distance, silhouette."""
    n = len(sampled)
    multi_auto = sum(1 for c in sampled if len(coded_by_chunk.get(c, set())) >= 2)
    multi_keyword = sum(1 for c in sampled if len(keyword_codes.get(c, set())) >= 2)

    centroid_distance: dict[str, object] = {"value": None, "n_codes": len(centroids)}
    if len(centroids) >= 2:
        matrix = code_centroid_matrix([centroids[c] for c in code_ids if c in centroids])
        k = len(matrix)
        pairs = k * (k - 1)
        if pairs:
            offdiag = float(matrix.sum()) - k  # gram diagonal is 1.0
            centroid_distance = {"value": 1.0 - offdiag / pairs, "n_codes": k}

    single = [
        c
        for c in sampled
        if len(coded_by_chunk.get(c, set())) == 1 and next(iter(coded_by_chunk[c])) in code_ids
    ]
    label_of = {c: code_ids.index(next(iter(coded_by_chunk[c]))) for c in single}
    silhouette_report: dict[str, object] = {"value": None, "n_groups": 0}
    if len(set(label_of.values())) >= 2:
        scores = silhouette_by_group([vectors[c] for c in single], [label_of[c] for c in single])
        vals = [float(v) for v in scores.values()]
        if vals:
            silhouette_report = {"value": sum(vals) / len(vals), "n_groups": len(vals)}
    return {
        "multi_automatic_code_share": share(multi_auto, n),
        "multi_keyword_code_share": share(multi_keyword, n),
        "mean_centroid_cosine_distance": centroid_distance,
        "silhouette": silhouette_report,
    }


def _residue_stability(
    residue: list[str], vectors: dict[str, list[float]], texts: dict[str, str]
) -> tuple[dict[str, object], list[dict[str, object]]]:
    """ARI stability and top terms for the residue clusters (needs >= 6 chunks)."""
    stability: dict[str, object] = {"mean_pairwise_ari": None, "n_resamples": 0}
    cluster_terms: list[dict[str, object]] = []
    if len(residue) >= 6:
        labels = [int(x) for x in spherical_kmeans([vectors[c] for c in residue], 3)]
        boot = bootstrap_stability([vectors[c] for c in residue], n_resamples=5)
        stability = {
            "mean_pairwise_ari": boot.get("mean_pairwise_ari"),
            "stable": boot.get("stable"),
            "n_resamples": 5,
        }
        by_label: dict[int, list[str]] = {}
        for chunk_id, label in zip(residue, labels, strict=True):
            by_label.setdefault(label, []).append(texts[chunk_id])
        cluster_terms = residue_top_terms(by_label)
    return stability, cluster_terms


def mece_block(inputs: dict[str, Any]) -> dict[str, Any]:
    """Exclusivity, exhaustiveness, and stability over the fetched state."""
    vectors: dict[str, list[float]] = {}
    texts: dict[str, str] = {}
    embedder_counts: Counter[str] = Counter()
    for row in inputs["vector_rows"]:
        parsed = parse_vector(row["embedding"])
        if parsed is not None:
            chunk_id = str(row["chunk_id"])
            vectors[chunk_id] = parsed
            texts[chunk_id] = str(row["text"] or "")
            embedder_counts[str(row["embedding_model"])] += 1
    embedder = embedder_counts.most_common(1)[0][0] if embedder_counts else "unknown"

    codes = inputs["codes"]
    code_ids = [str(r["code_id"]) for r in codes]
    centroids: dict[str, list[float]] = {}
    for row in codes:
        parsed = parse_vector(row["definition_vector"])
        if parsed is not None:
            centroids[str(row["code_id"])] = parsed

    coded_by_chunk: dict[str, set[str]] = {}
    for row in inputs["coded"]:
        chunk_id, code_id = str(row["chunk_id"]), str(row["code_id"])
        if code_id in centroids:
            coded_by_chunk.setdefault(chunk_id, set()).add(code_id)
    keyword_codes: dict[str, set[str]] = {}
    for row in inputs["keyword_hits"]:
        keyword_codes.setdefault(str(row["chunk_id"]), set()).add(str(row["code_id"]))

    sampled = sorted(vectors)
    n = len(sampled)
    residue = [c for c in sampled if not coded_by_chunk.get(c)]
    exclusivity = _exclusivity_block(
        sampled, vectors, coded_by_chunk, keyword_codes, code_ids, centroids
    )
    stability, cluster_terms = _residue_stability(residue, vectors, texts)

    exhaustiveness: dict[str, object] = {
        "coded_share": share(n - len(residue), n),
        "residue_size": len(residue),
        "residue_clusters": cluster_terms,
        "stability": stability,
    }
    if centroids and residue:
        protos = {c: {"answer": [centroids[c]]} for c in centroids}
        res = residuals({"answer": [vectors[c] for c in residue]}, protos)
        exhaustiveness["residual"] = residual_section(
            {"all": [float(x) for x in res]}, {"all": TAU_RES_FLOOR}
        )["pooled"]
    return {
        "exclusivity": exclusivity,
        "exhaustiveness": exhaustiveness,
        "embedder": embedder,
        "n_chunks": n,
        "n_chunks_capped": len(inputs["vector_rows"]) >= MAX_CHUNKS,
    }
