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
module: every class must hold at least ``cv_folds`` members, or the
answer is the honest ``insufficient_data`` envelope naming the thinnest
class.
"""

from __future__ import annotations

import asyncpg
import numpy as np

from ..analysis.vectors import VectorSpace
from ..coding.label_triage import (
    OK,
    LabelReviewItem,
    label_triage,
    logistic_fold_predictor,
)
from ..errors import ValidationError
from .ingest_common import DEFAULT_TENANT_ID

__all__ = ["labeling_triage"]

# Read/list bounds and op defaults (the labeling_tools conventions).
LABEL_READ_LIMIT = 200_000
DEFAULT_TRIAGE_FOLDS = 5
DEFAULT_FLAG_THRESHOLD = 0.2
DEFAULT_QUEUE_LIMIT = 50
MAX_QUEUE_LIMIT = 500

_TRIAGE_SEED = 0  # pinned: one build, one queue (deterministic output rule)

_UNAVAILABLE_REASON = "labeling tables absent (migration 028 not applied)"
_UNAVAILABLE_REMEDY = "run corpus-kb setup"


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
    folds = _validated_folds(cv_folds)
    threshold = _validated_threshold(flag_threshold)
    bounded = _validated_limit(limit)
    del config  # accepted for MCP-tool signature symmetry; reads are config-free
    if not await _tables_present(pg_pool):
        return _honest("unavailable", _UNAVAILABLE_REASON, _UNAVAILABLE_REMEDY, folds, threshold)
    space = await _load_vectors(pg_pool, tenant_id, None)
    label_map = _label_map(await _load_labels(pg_pool, tenant_id))
    ids = [chunk_id for chunk_id in space.ids if chunk_id in label_map]
    if not ids:
        return _honest(
            "insufficient_data",
            "no built labels with stored vectors for this tenant.",
            "Run labeling-build to label chunks, then retry.",
            folds,
            threshold,
        )
    classes = sorted({label_map[chunk_id] for chunk_id in ids})
    index_of = {name: position for position, name in enumerate(classes)}
    labels = [index_of[label_map[chunk_id]] for chunk_id in ids]
    thin = _thinnest_class(classes, labels, folds)
    if thin is not None:
        name, count = thin
        return _honest(
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
    result = label_triage(
        space.matrix[[positions[chunk_id] for chunk_id in ids]],
        np.array([index_of[label_map[chunk_id]] for chunk_id in ids], dtype=int),
        fold_predictor=logistic_fold_predictor(),
        cv_folds=folds,
        flag_threshold=threshold,
        seed=_TRIAGE_SEED,
    )
    if result.status != OK:
        return _honest(
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
        "queue": [_queue_row(item, ids, classes) for item in head],
        "n_units": result.n_units,
        "cv_folds": result.cv_folds,
        "flag_threshold": result.flag_threshold,
        "classes": classes,
        "reason": result.reason,
    }


def _queue_row(item: LabelReviewItem, ids: list[str], classes: list[str]) -> dict[str, object]:
    """One queue row: indices resolved to chunk ids and class names."""
    return {
        "chunk_id": ids[item.unit_index],
        "assigned_label": classes[item.assigned_label],
        "oos_probability": item.oos_probability,
        "runner_up_label": classes[item.runner_up_label],
        "runner_up_probability": item.runner_up_probability,
    }


def _thinnest_class(classes: list[str], labels: list[int], folds: int) -> tuple[str, int] | None:
    """(name, count) when a class sits below the fold floor, else None.

    A class with fewer members than ``cv_folds`` can leave one fold's
    train split without it, and the module's per-fold shape check would
    raise a raw error. The guard refuses that state up front.
    """
    counts = [0] * len(classes)
    for label in labels:
        counts[label] += 1
    name, count = min(zip(classes, counts, strict=True), key=lambda item: item[1])
    if count < folds:
        return name, count
    return None


def _honest(
    status: str,
    reason: str,
    remedy: str,
    folds: int,
    threshold: float,
    n_units: int = 0,
    classes: list[str] | None = None,
) -> dict[str, object]:
    """The guard envelope on the triage keys (reason + remedy named)."""
    return {
        "status": status,
        "n_flagged": 0,
        "queue": [],
        "n_units": n_units,
        "cv_folds": folds,
        "flag_threshold": threshold,
        "classes": classes or [],
        "reason": reason,
        "remedy": remedy,
    }


def _label_map(rows: list[dict[str, object]]) -> dict[str, str]:
    """chunk_id -> label; the first row wins if the store ever repeats."""
    label_map: dict[str, str] = {}
    for row in rows:
        chunk_id = str(row["chunk_id"])
        if chunk_id not in label_map:
            label_map[chunk_id] = str(row["label"])
    return label_map


def _validated_folds(cv_folds: int) -> int:
    """One fold would see its own rows; two is the honest floor."""
    if isinstance(cv_folds, bool) or not isinstance(cv_folds, int) or cv_folds < 2:
        raise ValidationError("cv_folds must be a whole number of 2 or more.")
    return int(cv_folds)


def _validated_threshold(flag_threshold: float) -> float:
    """The flag threshold is a probability: inside (0, 1]."""
    if (
        isinstance(flag_threshold, bool)
        or not isinstance(flag_threshold, (int, float))
        or not 0.0 < float(flag_threshold) <= 1.0
    ):
        raise ValidationError("flag_threshold must be a number greater than 0 and at most 1.")
    return float(flag_threshold)


def _validated_limit(limit: int) -> int:
    """The queue head bound (the labeling read bounds)."""
    if isinstance(limit, bool) or not isinstance(limit, int) or limit < 1:
        raise ValidationError("limit must be a whole number of 1 or more.")
    return min(int(limit), MAX_QUEUE_LIMIT)
