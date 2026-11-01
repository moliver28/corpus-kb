"""MCP/HTTP/socket/CLI handler for the labeling triage op (U39).

One handler shared by every surface — the ``tools/labeling_tools.py``
pattern. :func:`labeling_triage` reads the tenant's built chunk labels
(the ``chunk_labels`` store from a labeling build) plus the stored chunk
embeddings, and ranks the units whose ASSIGNED label has weak
out-of-sample support. The ranking itself is ``coding/label_triage.py``
(confident learning from scratch; Cleanlab is AGPL and rejected).

Advisory only: the only output is a ranked review queue. Nothing is
relabelled, and the queue never writes.

Deterministic: folds are assigned under a fixed seed and the fold
predictor is the module's numpy logistic factory, so one tenant build
always yields the same queue. ``n_flagged`` carries the honest total;
``queue`` is the worst-support head of it, bounded by ``limit`` (the
``labeling_build`` sample rule).

Envelope: ``{status, n_flagged, queue}`` guaranteed on every outcome,
plus ``reason`` everywhere and ``remedy`` on the guard paths. Statuses:
``success``, ``insufficient_data`` (no labels, no vectors, too few units,
a class below the fold floor), ``unavailable`` (migration 028 not
applied).

Fold-coverage guard: the injected predictor derives its class count
from the train split, so a class with fewer members than ``cv_folds``
can leave a fold's train split without that class and make the per-fold
shape check fail. The handler refuses that state BEFORE calling the
module (``ops.thinnest_class``): every class must hold at least
``cv_folds`` members, or the answer is the honest ``insufficient_data``
envelope naming the thinnest class.
"""

from __future__ import annotations

import asyncpg
import numpy as np

from ..analysis.vectors import VectorSpace
from ..coding.label_triage import OK, label_triage, logistic_fold_predictor
from .ingest_common import DEFAULT_TENANT_ID
from .labeling_triage_ops import (
    _TRIAGE_SEED,
    _UNAVAILABLE_REASON,
    _UNAVAILABLE_REMEDY,
    DEFAULT_FLAG_THRESHOLD,
    DEFAULT_QUEUE_LIMIT,
    DEFAULT_TRIAGE_FOLDS,
    honest_envelope,
    label_map,
    queue_row,
    thinnest_class,
    validated_folds,
    validated_limit,
    validated_threshold,
)

__all__ = ["labeling_triage"]

# The labeled-corpus read bound (the engine's read-batch scale).
LABEL_READ_LIMIT = 200_000


async def _load_vectors(
    pg_pool: asyncpg.Pool, tenant_id: str, config: dict[str, object] | None
) -> VectorSpace:
    """The vector seam (tests substitute fakes here)."""
    from corpus_kb.analysis.vectors import load_tenant_vectors

    return await load_tenant_vectors(pg_pool, tenant_id, config)


async def _load_labels(pg_pool: asyncpg.Pool, tenant_id: str) -> list[dict[str, object]]:
    """The label seam: the labeling store's built labels (tests fake it)."""
    from corpus_kb.labeling.writer import read_labels

    return await read_labels(pg_pool, tenant_id, LABEL_READ_LIMIT)


async def _tables_present(pg_pool: asyncpg.Pool) -> bool:
    """The store seam (tests substitute fakes here)."""
    from corpus_kb.labeling.writer import labeling_tables_present

    return await labeling_tables_present(pg_pool)


async def labeling_triage(
    pg_pool: asyncpg.Pool,
    config: dict[str, object] | None = None,
    tenant_id: str = DEFAULT_TENANT_ID,
    cv_folds: int = DEFAULT_TRIAGE_FOLDS,
    flag_threshold: float = DEFAULT_FLAG_THRESHOLD,
    limit: int = DEFAULT_QUEUE_LIMIT,
) -> dict[str, object]:
    """Rank built labels with weak out-of-sample support for review.

    Returns ``{status, n_flagged, queue}`` plus the params record. Each
    queue row is ``{chunk_id, assigned_label, oos_probability,
    runner_up_label, runner_up_probability}``, worst support first.
    """
    folds = validated_folds(cv_folds)
    threshold = validated_threshold(flag_threshold)
    bounded = validated_limit(limit)
    del config  # accepted for MCP-tool signature symmetry; reads are config-free
    if not await _tables_present(pg_pool):
        return honest_envelope(
            "unavailable", _UNAVAILABLE_REASON, _UNAVAILABLE_REMEDY, folds, threshold
        )
    space = await _load_vectors(pg_pool, tenant_id, None)
    labels_by_id = label_map(await _load_labels(pg_pool, tenant_id))
    ids = [chunk_id for chunk_id in space.ids if chunk_id in labels_by_id]
    if not ids:
        return honest_envelope(
            "insufficient_data",
            "no built labels with stored vectors for this tenant.",
            "Run labeling-build to label chunks, then retry.",
            folds,
            threshold,
        )
    classes = sorted({labels_by_id[chunk_id] for chunk_id in ids})
    index_of = {name: position for position, name in enumerate(classes)}
    labels = [index_of[labels_by_id[chunk_id]] for chunk_id in ids]
    thin = thinnest_class(classes, labels, folds)
    if thin is not None:
        name, count = thin
        return honest_envelope(
            "insufficient_data",
            f"the thinnest class {name!r} holds only {count} of the needed {folds} labeled units.",
            "Label more chunks, then retry.",
            folds,
            threshold,
            n_units=len(ids),
            classes=classes,
        )
    positions = {chunk_id: index for index, chunk_id in enumerate(space.ids)}
    result = label_triage(
        space.matrix[[positions[chunk_id] for chunk_id in ids]],
        np.array(labels, dtype=int),
        fold_predictor=logistic_fold_predictor(),
        cv_folds=folds,
        flag_threshold=threshold,
        seed=_TRIAGE_SEED,
    )
    if result.status != OK:
        return honest_envelope(
            "insufficient_data",
            result.reason,
            "Label more chunks, then retry.",
            folds,
            threshold,
            n_units=result.n_units,
            classes=classes,
        )
    head = result.queue[:bounded]
    return {
        "status": "success",
        "n_flagged": result.n_flagged,
        "queue": [queue_row(item, ids, classes) for item in head],
        "n_units": result.n_units,
        "cv_folds": result.cv_folds,
        "flag_threshold": result.flag_threshold,
        "classes": classes,
        "reason": result.reason,
    }
