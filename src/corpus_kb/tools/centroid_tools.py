"""MCP/HTTP/socket/CLI handlers for the centroid classifier (AMR program).

:func:`centroids_classify` — one centroid per label (the mean of that
label's chunk vectors), cosine similarity from each target chunk to
every centroid, top label wins. When the margin between the best and
second-best similarity is under ``abstain_margin`` the row ABSTAINS:
``abstained: true`` rides the prediction, the honest signal that the
evidence is too close to call (the AMR honesty rule — no fake
confidence at the decision boundary).

Determinism contract (byte-equal on one machine): labels iterate in
sorted order, ties break to the alphabetically first label, and
predictions sort by target chunk_id ascending. Pure numpy over
:mod:`analysis.vectors`; no embedder call.
"""

from __future__ import annotations

import asyncpg
import numpy as np

from ..analysis import vectors
from ..errors import ValidationError
from .ingest_common import DEFAULT_TENANT_ID
from .neighbor_tools import (
    _EMPTY_CORPUS_REASON,
    _EMPTY_CORPUS_REMEDY,
    _NO_LABELS_REASON,
    _NO_LABELS_REMEDY,
    _NO_TARGETS_REASON,
    _NO_TARGETS_REMEDY,
    _empty,
    _label_map,
    _load_labels,
    _targets,
)

__all__ = ["centroids_classify"]


async def _load_vectors(
    pg_pool: asyncpg.Pool, tenant_id: str, config: dict[str, object] | None
) -> vectors.VectorSpace:
    """The vector seam (tests substitute fakes here)."""
    return await vectors.load_tenant_vectors(pg_pool, tenant_id, config)


def _centroids(
    units: np.ndarray, space_ids: list[str], label_map: dict[str, str]
) -> tuple[list[str], np.ndarray]:
    """Per-label unit centroids (sorted labels); means over member rows."""
    label_names = sorted(set(label_map.values()))
    by_label: dict[str, list[int]] = {label: [] for label in label_names}
    for index, chunk_id in enumerate(space_ids):
        label = label_map.get(chunk_id)
        if label is not None:
            by_label[label].append(index)
    rows = [
        vectors.unit_rows(units[by_label[label]].mean(axis=0, keepdims=True))[0]
        for label in label_names
    ]
    return label_names, np.stack(rows)


async def centroids_classify(
    pg_pool: asyncpg.Pool,
    config: dict[str, object] | None = None,
    tenant_id: str = DEFAULT_TENANT_ID,
    abstain_margin: float = 0.05,
    chunk_ids: list[str] | None = None,
) -> dict[str, object]:
    """Nearest-centroid labels for the requested (default: unlabeled) chunks.

    Returns ``{status, predictions, screened_count}``. Each prediction
    row is ``{chunk_id, label, abstained, margin}``: ``margin`` is the
    6-place gap between the best and second-best similarity (the second
    best is -1.0 when only one label exists, so a single-label corpus
    never abstains). ``abstained`` is true when ``margin`` is below
    ``abstain_margin`` — the label is still reported, as a suggestion.
    """
    margin = float(abstain_margin)
    if not 0.0 <= margin <= 2.0:
        raise ValidationError("abstain_margin must sit between 0 and 2.")
    space = await _load_vectors(pg_pool, tenant_id, config)
    if not space.ids:
        return _empty("empty", _EMPTY_CORPUS_REASON, _EMPTY_CORPUS_REMEDY, 0)
    label_map = _label_map(await _load_labels(pg_pool, tenant_id))
    if not any(chunk_id in label_map for chunk_id in space.ids):
        return _empty("empty", _NO_LABELS_REASON, _NO_LABELS_REMEDY, len(space.ids))
    targets = _targets(space, label_map, chunk_ids)
    if not targets:
        return _empty("empty", _NO_TARGETS_REASON, _NO_TARGETS_REMEDY, len(space.ids))
    with vectors.pin_numeric_threads():
        units = vectors.unit_rows(space.matrix)
        label_names, centroid_matrix = _centroids(units, space.ids, label_map)
        id_positions = {chunk_id: index for index, chunk_id in enumerate(space.ids)}
        predictions: list[dict[str, object]] = []
        for target in targets:
            sims = units[id_positions[target]] @ centroid_matrix.T
            order = sorted(range(len(label_names)), key=lambda p: (-float(sims[p]), label_names[p]))
            top, second = order[0], order[1] if len(order) > 1 else None
            gap = float(sims[top]) - (float(sims[second]) if second is not None else -1.0)
            predictions.append(
                {
                    "chunk_id": target,
                    "label": label_names[top],
                    "abstained": bool(gap < margin),
                    "margin": round(gap, 6),
                }
            )
    return {"status": "success", "predictions": predictions, "screened_count": len(space.ids)}
