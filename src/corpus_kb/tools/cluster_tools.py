"""MCP/HTTP/socket/CLI handler for clusters_inspect (AMR program).

:func:`clusters_inspect` — reduce the tenant's vectors, cluster them
with HDBSCAN, and report the honest shape: cluster sizes, noise count,
and exemplar chunk ids (the members nearest each cluster centroid).

Determinism contract (the AMR rule for cluster ops — STRUCTURE, not
coordinates): same data on one machine gives the same cluster sizes,
noise count, and exemplar ids. Coordinate byte-equality is never
claimed.

Reduction: the pinned UMAP from ``topics.engine`` (``random_state=42``,
the repo-wide research pin) when umap is installed; otherwise an honest
sklearn PCA fallback (full SVD, deterministic) and the envelope's
``params.reduction`` names what ran. Clustering: sklearn HDBSCAN
(eom), deterministic given the reduced space.
"""

from __future__ import annotations

import asyncpg
import numpy as np

from ..analysis import vectors
from ..errors import DependencyMissingError, ValidationError
from .ingest_common import DEFAULT_TENANT_ID

__all__ = ["clusters_inspect"]

# HDBSCAN's own default min_cluster_size; the explicit code pin.
MIN_CLUSTER_SIZE_DEFAULT = 5

# Exemplar chunk ids reported per cluster (nearest the centroid).
EXEMPLAR_COUNT = 3

# The research pin carried into the params record (UMAP + sampler).
RANDOM_STATE = 42

_EMPTY_REASON = "the tenant has no embedded chunks to cluster."
_EMPTY_REMEDY = "Ingest documents with embeddings first, then retry."
_SINGLE_REASON = "the tenant has fewer than two embedded chunks; no cluster structure exists."
_SINGLE_REMEDY = "Ingest more documents, then retry."

_CLASSIFY_INSTALL = (
    "The clusters feature needs scikit-learn. Install the extra: pip install 'corpus-kb[classify]'."
)


class _ClusterDependencyError(DependencyMissingError):
    """The ``[classify]`` extra is not installed (sklearn HDBSCAN/PCA)."""

    remediation = _CLASSIFY_INSTALL


async def _load_vectors(
    pg_pool: asyncpg.Pool, tenant_id: str, config: dict[str, object] | None
) -> vectors.VectorSpace:
    """The vector seam (tests substitute fakes here)."""
    return await vectors.load_tenant_vectors(pg_pool, tenant_id, config)


def _reduce(matrix: np.ndarray) -> tuple[np.ndarray, str]:
    """Reduce to the clustering space; name what ran (the honesty key).

    UMAP through the topics engine's pinned construction when umap is
    installed (the importlib indirection keeps the extras-less runtime
    and the type checker clean); PCA (full SVD) otherwise. Tests
    substitute fakes here.
    """
    try:
        # The engine's indirection stays invisible to deptry (a direct
        # importlib.import_module literal is DEP001-flagged; the extras'
        # umap-learn ships transitively under [topics]).
        from corpus_kb.topics.engine import _import_module

        _import_module("umap")
    except ImportError:
        return _pca_reduce(matrix)
    from corpus_kb.topics.engine import _make_umap, load_umap

    # The inspect sample can be tiny; umap truncates n_neighbors to n-1
    # with a UserWarning on small corpora — clamp here, same contract as
    # the inductive engine, so the inspect path stays silent.
    n_neighbors = min(15, max(matrix.shape[0] - 1, 2))
    reducer = _make_umap(load_umap(), n_neighbors=n_neighbors)
    return np.asarray(reducer.fit_transform(matrix), dtype=np.float64), "umap"


def _pca_reduce(matrix: np.ndarray) -> tuple[np.ndarray, str]:
    """The deterministic fallback (full-SVD PCA; no random state needed)."""
    try:
        from sklearn.decomposition import PCA
    except ImportError as exc:
        raise _ClusterDependencyError(_CLASSIFY_INSTALL) from exc
    count = min(5, matrix.shape[0] - 1, matrix.shape[1])
    reducer = PCA(n_components=count, svd_solver="full")
    return np.asarray(reducer.fit_transform(matrix), dtype=np.float64), "pca_fallback"


def _cluster(reduced: np.ndarray, min_cluster_size: int) -> np.ndarray:
    """HDBSCAN (eom) over the reduced space; -1 labels are noise."""
    try:
        from sklearn.cluster import HDBSCAN
    except ImportError as exc:
        raise _ClusterDependencyError(_CLASSIFY_INSTALL) from exc
    model = HDBSCAN(min_cluster_size=min_cluster_size, cluster_selection_method="eom")
    return np.asarray(model.fit_predict(reduced))


def _sample_indices(total: int, cap: int | None) -> list[int]:
    """All indices, or the seeded sorted subsample (the engine's pin)."""
    if cap is None or total <= cap:
        return list(range(total))
    from corpus_kb.topics.engine import _sample_index

    return _sample_index(total, cap)


async def clusters_inspect(
    pg_pool: asyncpg.Pool,
    config: dict[str, object] | None = None,
    tenant_id: str = DEFAULT_TENANT_ID,
    min_cluster_size: int | None = None,
    sample_cap: int | None = None,
) -> dict[str, object]:
    """Report the corpus cluster structure; never claims coordinates.

    Returns ``{status, clusters, noise_count, screened_count, params}``.
    Each cluster row is ``{cluster_id, size, exemplar_chunk_ids}`` with
    rows ordered size desc then cluster_id asc; exemplars (up to
    ``EXEMPLAR_COUNT``) are the members nearest the centroid in the
    reduced space, distance asc then chunk_id asc. ``sample_cap`` above
    the corpus size does nothing; at or below it the scan subsamples
    with the seeded pin and ``screened_count`` reports the real size.
    """
    if min_cluster_size is not None and (
        isinstance(min_cluster_size, bool)
        or not isinstance(min_cluster_size, int)
        or min_cluster_size < 2
    ):
        raise ValidationError("min_cluster_size must be a whole number of 2 or more.")
    if sample_cap is not None and (
        isinstance(sample_cap, bool) or not isinstance(sample_cap, int) or sample_cap < 2
    ):
        raise ValidationError("sample_cap must be a whole number of 2 or more.")
    space = await _load_vectors(pg_pool, tenant_id, config)
    total = len(space.ids)
    if total == 0:
        return _empty_envelope(_EMPTY_REASON, _EMPTY_REMEDY, 0)
    if total < 2:
        return _empty_envelope(_SINGLE_REASON, _SINGLE_REMEDY, total)
    indices = _sample_indices(total, sample_cap)
    ids = [space.ids[i] for i in indices]
    # HDBSCAN needs min_cluster_size <= the sample count; a cap below
    # the requested size shrinks it (the clamped value rides params).
    effective_min = MIN_CLUSTER_SIZE_DEFAULT if min_cluster_size is None else min_cluster_size
    effective_min = min(effective_min, len(indices))
    with vectors.pin_numeric_threads():
        reduced, reduction = _reduce(space.matrix[indices])
        labels = _cluster(reduced, effective_min)
    members: dict[int, list[int]] = {}
    noise_count = 0
    for position, label in enumerate(labels.tolist()):
        if label < 0:
            noise_count += 1
        else:
            members.setdefault(int(label), []).append(position)
    built: list[tuple[int, int, list[str]]] = []  # (size, cluster_id, exemplars)
    for cluster_id in sorted(members):
        positions = members[cluster_id]
        centroid = reduced[positions].mean(axis=0)
        ranked = sorted(
            positions,
            key=lambda p: (
                float(np.linalg.norm(reduced[p] - centroid)),
                ids[p],
            ),
        )
        built.append((len(positions), cluster_id, [ids[p] for p in ranked[:EXEMPLAR_COUNT]]))
    built.sort(key=lambda entry: (-entry[0], entry[1]))
    rows: list[dict[str, object]] = [
        {"cluster_id": cluster_id, "size": size, "exemplar_chunk_ids": exemplars}
        for size, cluster_id, exemplars in built
    ]
    return {
        "status": "success",
        "clusters": rows,
        "noise_count": noise_count,
        "screened_count": len(indices),
        "params": {
            "reduction": reduction,
            "random_state": RANDOM_STATE,
            "min_cluster_size": effective_min,
        },
    }


def _empty_envelope(reason: str, remedy: str, screened: int) -> dict[str, object]:
    """The honest no-structure envelope on this op's contract keys."""
    return {
        "status": "empty",
        "reason": reason,
        "remedy": remedy,
        "clusters": [],
        "noise_count": 0,
        "screened_count": screened,
    }
