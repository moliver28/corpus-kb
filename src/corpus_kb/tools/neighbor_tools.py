"""MCP/HTTP/socket/CLI handlers for the kNN classifier (AMR program).

:func:`neighbors_classify` — k-nearest-neighbor voting over the tenant's
LABELED chunks (the ``chunk_labels`` store from a labeling build), used
to predict labels for the requested (default: unlabeled) chunks.

Determinism contract (byte-equal on one machine):

- neighbors per target: similarity desc, then chunk_id asc;
- votes: count desc, then label asc (so a vote tie goes to the
  alphabetically first label — the chunk_id rule has already fixed the
  neighbor set itself);
- predictions: target chunk_id ascending.

Pure numpy over :mod:`analysis.vectors`; no embedder call. ``k``
larger than the labeled count shrinks to the labeled count.
"""

from __future__ import annotations

import asyncpg
import numpy as np

from ..analysis import vectors
from ..errors import ValidationError
from .ingest_common import DEFAULT_TENANT_ID

__all__ = ["neighbors_classify"]

# The labeled-corpus read bound (the engine's read-batch scale; a
# labeling build writes one row per chunk, so this bounds the op).
LABEL_READ_LIMIT = 200_000

_NO_LABELS_REASON = "no labeled chunks with stored vectors for this tenant."
_NO_LABELS_REMEDY = "Run labeling-build to label chunks, then retry."
_NO_TARGETS_REASON = "none of the requested chunk ids have stored vectors."
_NO_TARGETS_REMEDY = "List chunk ids that exist in this tenant, then retry."
_EMPTY_CORPUS_REASON = "the tenant has no embedded chunks to classify."
_EMPTY_CORPUS_REMEDY = "Ingest documents with embeddings first, then retry."


async def _load_vectors(
    pg_pool: asyncpg.Pool, tenant_id: str, config: dict[str, object] | None
) -> vectors.VectorSpace:
    """The vector seam (tests substitute fakes here)."""
    return await vectors.load_tenant_vectors(pg_pool, tenant_id, config)


async def _load_labels(pg_pool: asyncpg.Pool, tenant_id: str) -> list[dict[str, object]]:
    """The label seam: the labeling store's chunk labels (tests fake it)."""
    from corpus_kb.labeling.writer import read_labels

    return await read_labels(pg_pool, tenant_id, LABEL_READ_LIMIT)


def _label_map(labels: list[dict[str, object]]) -> dict[str, str]:
    """chunk_id -> label; the first row wins if the store ever repeats."""
    label_map: dict[str, str] = {}
    for row in labels:
        chunk_id = str(row["chunk_id"])
        if chunk_id not in label_map:
            label_map[chunk_id] = str(row["label"])
    return label_map


def _targets(
    space: vectors.VectorSpace, label_map: dict[str, str], chunk_ids: list[str] | None
) -> list[str]:
    """Prediction targets: explicit ids (present, deduped, sorted) or the
    tenant's unlabeled ids in the space's ascending order."""
    if chunk_ids is not None:
        wanted = {str(chunk_id) for chunk_id in chunk_ids}
        return [chunk_id for chunk_id in space.ids if chunk_id in wanted]
    return [chunk_id for chunk_id in space.ids if chunk_id not in label_map]


def _empty(status: str, reason: str, remedy: str, screened: int) -> dict[str, object]:
    """The honest no-data envelope on this family's contract keys."""
    return {
        "status": status,
        "reason": reason,
        "remedy": remedy,
        "predictions": [],
        "screened_count": screened,
    }


def _vote_label(
    positions: list[int], labeled_ids: list[str], label_map: dict[str, str]
) -> tuple[str, list[dict[str, object]], list[str]]:
    """Majority vote over one target's ordered neighbor positions."""
    counts: dict[str, int] = {}
    neighbor_ids: list[str] = []
    for position in positions:
        neighbor_id = labeled_ids[position]
        neighbor_ids.append(neighbor_id)
        label = label_map[neighbor_id]
        counts[label] = counts.get(label, 0) + 1
    ranked = sorted(counts.items(), key=lambda item: (-item[1], item[0]))
    return ranked[0][0], [{"label": label, "votes": count} for label, count in ranked], neighbor_ids


async def neighbors_classify(
    pg_pool: asyncpg.Pool,
    config: dict[str, object] | None = None,
    tenant_id: str = DEFAULT_TENANT_ID,
    k: int = 5,
    chunk_ids: list[str] | None = None,
) -> dict[str, object]:
    """kNN-vote labels for the requested (default: unlabeled) chunks.

    Returns ``{status, predictions, screened_count}``. Each prediction
    row is ``{chunk_id, label, votes, neighbor_ids}``; ``votes`` lists
    ``{label, votes}`` by count desc then label asc, and ``neighbor_ids``
    lists the k winners in selection order. A target passed explicitly
    via ``chunk_ids`` never votes for itself.
    """
    if isinstance(k, bool) or not isinstance(k, int) or k < 1:
        raise ValidationError("k must be a whole number of 1 or more.")
    space = await _load_vectors(pg_pool, tenant_id, config)
    if not space.ids:
        return _empty("empty", _EMPTY_CORPUS_REASON, _EMPTY_CORPUS_REMEDY, 0)
    label_map = _label_map(await _load_labels(pg_pool, tenant_id))
    labeled_ids = [chunk_id for chunk_id in space.ids if chunk_id in label_map]
    if not labeled_ids:
        return _empty("empty", _NO_LABELS_REASON, _NO_LABELS_REMEDY, len(space.ids))
    targets = _targets(space, label_map, chunk_ids)
    if not targets:
        return _empty("empty", _NO_TARGETS_REASON, _NO_TARGETS_REMEDY, len(space.ids))
    with vectors.pin_numeric_threads():
        units = vectors.unit_rows(space.matrix)
        id_positions = {chunk_id: index for index, chunk_id in enumerate(space.ids)}
        labeled_positions = [id_positions[chunk_id] for chunk_id in labeled_ids]
        labeled_matrix = units[labeled_positions]
        effective_k = min(int(k), len(labeled_ids))
        predictions: list[dict[str, object]] = []
        for target in targets:
            row = units[id_positions[target]] @ labeled_matrix.T
            if target in label_map:  # an explicit target never votes for itself
                row[labeled_ids.index(target)] = -np.inf
            order = sorted(range(len(labeled_ids)), key=lambda p: (-float(row[p]), labeled_ids[p]))
            label, votes, neighbor_ids = _vote_label(order[:effective_k], labeled_ids, label_map)
            predictions.append(
                {
                    "chunk_id": target,
                    "label": label,
                    "votes": votes,
                    "neighbor_ids": neighbor_ids,
                }
            )
    return {"status": "success", "predictions": predictions, "screened_count": len(space.ids)}
