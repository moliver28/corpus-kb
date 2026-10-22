"""MCP/HTTP/socket/CLI handlers for the vector screen ops (AMR program).

One handler per operation, shared by every surface (the
``tools/analysis_tools.py`` house shape):

  * :func:`duplicates_screen` — cosine similarity pairs at or above a
    threshold (near-duplicate text), best pairs first.
  * :func:`outliers_rank` — distance to the k-th nearest neighbor, far
   thest first (the density-screen outlier rank).

Both are pure numpy over :mod:`analysis.vectors` — no embedder call, no
LLM, byte-deterministic on one machine (BLAS pinned to one thread).
The scan is blocked (one 1024-row block at a time) so a 20k-chunk
tenant never materializes a full n-times-n matrix.
"""

from __future__ import annotations

import asyncpg
import numpy as np

from ..analysis import vectors
from ..errors import ValidationError
from .ingest_common import DEFAULT_TENANT_ID

__all__ = ["duplicates_screen", "outliers_rank"]

# Rows per similarity block (bounded working set; the engine's scale).
_BLOCK = 1024

_EMPTY_REASON = "the tenant has no embedded chunks to screen."
_EMPTY_REMEDY = "Ingest documents with embeddings first, then retry."
_SINGLE_CHUNK_REASON = "the tenant has only one embedded chunk; no neighbors exist."
_SINGLE_CHUNK_REMEDY = "Ingest more documents, then retry."


async def _load_vectors(
    pg_pool: asyncpg.Pool, tenant_id: str, config: dict[str, object] | None
) -> vectors.VectorSpace:
    """The vector seam (tests substitute fakes here)."""
    return await vectors.load_tenant_vectors(pg_pool, tenant_id, config)


def _require_int(name: str, value: int, floor: int) -> int:
    """A whole number at or above the floor, else a typed 400 error."""
    if isinstance(value, bool) or not isinstance(value, int) or value < floor:
        raise ValidationError(f"{name} must be a whole number of {floor} or more.")
    return int(value)


def _empty(
    status: str, pairs_key: str, reason: str, remedy: str, screened: int
) -> dict[str, object]:
    """The honest no-data envelope on this family's contract keys."""
    return {
        "status": status,
        "reason": reason,
        "remedy": remedy,
        pairs_key: [],
        "screened_count": screened,
    }


async def duplicates_screen(
    pg_pool: asyncpg.Pool,
    config: dict[str, object] | None = None,
    tenant_id: str = DEFAULT_TENANT_ID,
    threshold: float = 0.98,
    limit: int = 100,
) -> dict[str, object]:
    """Cosine pairs at or above ``threshold``, most similar first.

    Returns ``{status, pairs, threshold, screened_count}``. Each pair is
    ``{similarity, chunk_id_i, chunk_id_j}`` with ``chunk_id_i`` the
    smaller id. Order: similarity desc, then id_i asc, then id_j asc,
    computed on the 6-place rounded values; capped at ``limit``.
    Zero vectors are excluded by the loader (cosine undefined).
    """
    if not -1.0 <= float(threshold) <= 1.0:
        raise ValidationError("threshold must sit between -1 and 1.")
    _require_int("limit", limit, 1)
    space = await _load_vectors(pg_pool, tenant_id, config)
    if not space.ids:
        return _empty("empty", "pairs", _EMPTY_REASON, _EMPTY_REMEDY, 0)
    with vectors.pin_numeric_threads():
        units = vectors.unit_rows(space.matrix)
        found: list[tuple[float, str, str]] = []
        for start in range(0, len(space.ids), _BLOCK):
            sims = units[start : start + _BLOCK] @ units.T
            for row_index, row in enumerate(sims):
                i = start + row_index
                for j in np.nonzero(row[:i] >= threshold)[0]:
                    found.append((float(row[j]), space.ids[j], space.ids[i]))
    rounded = [(round(score, 6), id_i, id_j) for score, id_i, id_j in found]
    rounded.sort(key=lambda triple: (-triple[0], triple[1], triple[2]))
    pairs = [
        {"similarity": score, "chunk_id_i": id_i, "chunk_id_j": id_j}
        for score, id_i, id_j in rounded[:limit]
    ]
    return {
        "status": "success",
        "pairs": pairs,
        "threshold": round(float(threshold), 6),
        "screened_count": len(space.ids),
    }


async def outliers_rank(
    pg_pool: asyncpg.Pool,
    config: dict[str, object] | None = None,
    tenant_id: str = DEFAULT_TENANT_ID,
    k: int = 10,
    limit: int = 20,
) -> dict[str, object]:
    """Rank chunks by distance to their k-th nearest neighbor (cosine).

    Returns ``{status, outliers, screened_count}``. Each row is
    ``{chunk_id, knn_distance}`` with ``knn_distance = 1 - similarity``
    to the k-th nearest OTHER chunk, rounded to 6 places. Order:
    distance desc, then chunk_id asc; capped at ``limit``. ``k`` larger
    than the corpus shrinks to corpus size minus one.
    """
    _require_int("k", k, 1)
    _require_int("limit", limit, 1)
    space = await _load_vectors(pg_pool, tenant_id, config)
    total = len(space.ids)
    if total == 0:
        return _empty("empty", "outliers", _EMPTY_REASON, _EMPTY_REMEDY, 0)
    if total == 1:
        return _empty("empty", "outliers", _SINGLE_CHUNK_REASON, _SINGLE_CHUNK_REMEDY, total)
    effective_k = min(int(k), total - 1)
    with vectors.pin_numeric_threads():
        units = vectors.unit_rows(space.matrix)
        rows: list[tuple[float, str]] = []
        for start in range(0, total, _BLOCK):
            sims = units[start : start + _BLOCK] @ units.T
            for row_index, row in enumerate(sims):
                i = start + row_index
                row[i] = -np.inf  # a chunk is never its own neighbor
                kth_largest = float(
                    np.partition(row, total - 1 - effective_k)[total - 1 - effective_k]
                )
                rows.append((1.0 - kth_largest, space.ids[i]))
    rounded = [(round(distance, 6), chunk_id) for distance, chunk_id in rows]
    rounded.sort(key=lambda pair: (-pair[0], pair[1]))
    outliers = [
        {"chunk_id": chunk_id, "knn_distance": distance} for distance, chunk_id in rounded[:limit]
    ]
    return {"status": "success", "outliers": outliers, "screened_count": total}
