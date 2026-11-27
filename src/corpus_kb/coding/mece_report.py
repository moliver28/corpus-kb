"""MECE math over fetched coding state (TD-11, R9b) — pure, no I/O.

Composes the repo's existing math: ``research/overlap``,
``research/exhaustiveness``, ``research/cluster_stability``. The embedder
name rides every report (S9). Every share carries its n and a Wilson 95
percent interval (AM-02); the three continuous metrics carry a 95
percent bootstrap interval (TAO-T0-01): pair distances, chunk resamples,
and the ARI seed spread. Stored vectors only (wave law); the DB reads
live in :mod:`corpus_kb.coding.report_reads`.
"""

from __future__ import annotations

import json
import math
from collections import Counter
from typing import Any

import numpy as np

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

#: Cap on chunk vectors the report reads (ordered by chunk_id).
MAX_CHUNKS = 5000

#: Bootstrap draws + fixed seed for the intervals (deterministic, T0-01).
BOOTSTRAP_B = 500
BOOTSTRAP_SEED = 20261125


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


def percentile_interval(values: list[float]) -> list[float] | None:
    """The 2.5/97.5 percentile band over one bootstrap distribution."""
    if not values:
        return None
    low = float(np.percentile(np.asarray(values, dtype=float), 2.5))
    high = float(np.percentile(np.asarray(values, dtype=float), 97.5))
    return [low, high]


def _bootstrap_mean_interval(values: list[float], rng: np.random.Generator) -> list[float] | None:
    """95 percent bootstrap interval for a mean (resample with replacement)."""
    if not values:
        return None
    arr = np.asarray(values, dtype=float)
    means = [float(arr[rng.integers(0, len(arr), len(arr))].mean()) for _ in range(BOOTSTRAP_B)]
    return percentile_interval(means)


def parse_vector(raw: object) -> list[float] | None:
    """Stored vector -> floats; None when absent, unparsable, or all zeros.

    Shapes: a pgvector literal (asyncpg), a list (JSON), a tuple (DuckDB).
    """
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except ValueError:
            return None
    if not isinstance(raw, (list, tuple)) or not raw or not any(raw):
        return None
    return [float(x) for x in raw]


def _centroid_distance_block(
    code_ids: list[str], centroids: dict[str, list[float]]
) -> dict[str, object]:
    """Mean pairwise centroid distance + its pair-bootstrap interval (T0-01)."""
    if len(centroids) < 2:
        return {"value": None, "n_codes": len(centroids)}
    matrix = code_centroid_matrix([centroids[c] for c in code_ids if c in centroids])
    k = len(matrix)
    pairs = k * (k - 1)
    if not pairs:
        return {"value": None, "n_codes": len(centroids)}
    offdiag = float(matrix.sum()) - k  # gram diagonal is 1.0
    iu, ju = np.triu_indices(k, k=1)
    rng = np.random.default_rng(BOOTSTRAP_SEED)
    return {
        "value": 1.0 - offdiag / pairs,
        "n_codes": k,
        "interval": _bootstrap_mean_interval((1.0 - matrix[iu, ju]).tolist(), rng),
    }


def _silhouette_block(
    sampled: list[str],
    vectors: dict[str, list[float]],
    coded_by_chunk: dict[str, set[str]],
    code_ids: list[str],
) -> dict[str, object]:
    """Mean silhouette over single-coded chunks + its chunk bootstrap (T0-01)."""
    single = [
        c
        for c in sampled
        if len(coded_by_chunk.get(c, set())) == 1 and next(iter(coded_by_chunk[c])) in code_ids
    ]
    label_of = {c: code_ids.index(next(iter(coded_by_chunk[c]))) for c in single}
    if len(set(label_of.values())) < 2:
        return {"value": None, "n_groups": 0}
    scores = silhouette_by_group([vectors[c] for c in single], [label_of[c] for c in single])
    vals = [float(v) for v in scores.values()]
    if not vals:
        return {"value": None, "n_groups": 0}
    rng = np.random.default_rng(BOOTSTRAP_SEED)
    boot_means: list[float] = []
    for _ in range(BOOTSTRAP_B):
        draw = [single[i] for i in rng.integers(0, len(single), len(single))]
        draw_scores = silhouette_by_group([vectors[c] for c in draw], [label_of[c] for c in draw])
        if draw_scores:
            boot_means.append(sum(float(v) for v in draw_scores.values()) / len(draw_scores))
    return {
        "value": sum(vals) / len(vals),
        "n_groups": len(vals),
        "interval": percentile_interval(boot_means),
    }


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
    return {
        "multi_automatic_code_share": share(multi_auto, n),
        "multi_keyword_code_share": share(multi_keyword, n),
        "mean_centroid_cosine_distance": _centroid_distance_block(code_ids, centroids),
        "silhouette": _silhouette_block(sampled, vectors, coded_by_chunk, code_ids),
    }


def _residue_stability(
    residue: list[str], vectors: dict[str, list[float]], texts: dict[str, str]
) -> tuple[dict[str, object], list[dict[str, object]]]:
    """ARI stability + top terms (>= 6 chunks); TAO-T0-01 interval = seed spread."""
    if len(residue) < 6:
        return {"mean_pairwise_ari": None, "n_resamples": 0}, []
    labels = [int(x) for x in spherical_kmeans([vectors[c] for c in residue], 3)]
    runs = [
        bootstrap_stability([vectors[c] for c in residue], n_resamples=5, seed=seed)
        for seed in (42, 43, 44)
    ]
    observed: list[float] = []
    for run in runs:
        ari = run.get("mean_pairwise_ari")
        if isinstance(ari, (int, float)):
            observed.append(float(ari))
    by_label: dict[int, list[str]] = {}
    for chunk_id, label in zip(residue, labels, strict=True):
        by_label.setdefault(label, []).append(texts[chunk_id])
    return {
        "mean_pairwise_ari": observed[0] if observed else None,
        "stable": runs[0].get("stable"),
        "n_resamples": 5,
        "interval": percentile_interval(observed),
    }, residue_top_terms(by_label)


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
