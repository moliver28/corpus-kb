"""Read-only vector-space loading for the analysis ops (AMR program).

ONE accessor for every vector-space method (screens, neighbors,
centroids, clusters, the NLI candidate prefilter): the tenant's stored
chunk embeddings as a single float64 matrix. The read reuses the
classification family's storage path (same SQL shape, tenant GUC,
keyset batches) and never writes.

Contract:

- ids ascend by chunk_id (re-sorted here, so any read source is safe);
- ONE dimension per corpus: mixed dimensions raise ``ValidationError``
  before any numpy math can broadcast-fail (the AMR failure-path rule);
- zero and near-zero vectors are EXCLUDED — a zero vector has no
  direction, so cosine similarity is undefined for it — and the honest
  count rides the result.

The numeric pin: BLAS thread counts change floating-point summation
order, so the matrix math in this family runs under
:func:`pin_numeric_threads` (one thread when threadpoolctl is present,
unpinned when the optional helper is absent).
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass

import asyncpg
import numpy as np

from corpus_kb.classification.engine import StoredVector, read_stored_vectors
from corpus_kb.errors import ValidationError

__all__ = [
    "VectorSpace",
    "load_tenant_vectors",
    "load_tenant_vectors_with_texts",
    "pin_numeric_threads",
    "unit_rows",
]

# At or below this norm a vector is treated as zero (excluded, counted).
# Pinned constant: the exclusion must not move between runs.
ZERO_NORM_EPS = 1e-12


@dataclass(frozen=True)
class VectorSpace:
    """The tenant's embedded chunks: ids plus one aligned float64 matrix."""

    ids: list[str]
    matrix: np.ndarray  # shape (n, d), float64; row i is ids[i]
    excluded_zero_count: int


async def _read_stored(pg_pool: asyncpg.Pool, tenant_id: str) -> list[StoredVector]:
    """The storage seam (tests substitute fakes here).

    Delegates to the classification family's reader: tenant-scoped,
    keyset-paginated, ordered by chunk id, NULL embeddings excluded.
    """
    return await read_stored_vectors(pg_pool, tenant_id)


async def load_tenant_vectors(
    pg_pool: asyncpg.Pool,
    tenant_id: str,
    config: dict[str, object] | None = None,
) -> VectorSpace:
    """Load one tenant's vectors as ids + matrix; validate the space.

    Raises ``ValidationError`` when stored embeddings mix dimensions.
    ``config`` is accepted for handler symmetry and reserved for future
    governance knobs; the loader reads no keys today.
    """
    space, _texts = await load_tenant_vectors_with_texts(pg_pool, tenant_id, config)
    return space


async def load_tenant_vectors_with_texts(
    pg_pool: asyncpg.Pool,
    tenant_id: str,
    config: dict[str, object] | None = None,
) -> tuple[VectorSpace, dict[str, str]]:
    """The loader above plus one text map (chunk text by kept id).

    The NLI family needs the chunk text as its premise; the vector math
    does not. ONE read, ONE validation path — the text map covers the
    kept (non-zero) ids exactly, so a row filtered from the matrix can
    never resurface as a premise.
    """
    stored = await _read_stored(pg_pool, tenant_id)
    triples: list[tuple[str, str, np.ndarray]] = []
    dims: set[int] = set()
    for item in stored:
        vector = np.asarray(item.vector, dtype=np.float64).reshape(-1)
        dims.add(int(vector.shape[0]))
        triples.append((str(item.chunk_id), str(item.text), vector))
    if len(dims) > 1:
        low = sorted(dims)
        raise ValidationError(
            f"Chunk vectors use mixed dimensions: {low[0]} and {low[1]} (and maybe more).",
        )
    triples.sort(key=lambda entry: entry[0])
    ids = [chunk_id for chunk_id, _text, _row in triples]
    excluded = 0
    if triples:
        matrix = np.stack([row for _chunk_id, _text, row in triples])
        keep = np.linalg.norm(matrix, axis=1) > ZERO_NORM_EPS
        excluded = int(np.count_nonzero(~keep))
        matrix = matrix[keep]
        ids = []
        texts: dict[str, str] = {}
        for (chunk_id, text, _row), is_kept in zip(triples, keep.tolist(), strict=True):
            if is_kept:
                ids.append(chunk_id)
                texts[chunk_id] = text
    else:
        matrix = np.zeros((0, 0), dtype=np.float64)
        texts = {}
    return VectorSpace(ids=ids, matrix=matrix, excluded_zero_count=excluded), texts


def unit_rows(matrix: np.ndarray) -> np.ndarray:
    """Row-normalize to unit length; zero rows stay zero (never NaN)."""
    norms = np.linalg.norm(matrix, axis=1, keepdims=True)
    safe = np.where(norms == 0.0, 1.0, norms)
    return matrix / safe


@contextmanager
def pin_numeric_threads() -> Iterator[None]:
    """Pin BLAS to one thread when threadpoolctl is available (lazy)."""
    try:
        from threadpoolctl import threadpool_limits
    except ImportError:
        yield
        return
    with threadpool_limits(limits=1):
        yield
