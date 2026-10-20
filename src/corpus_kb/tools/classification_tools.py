"""MCP/HTTP/socket/CLI handlers for the classification ops (WP-3).

One handler per operation, shared by every surface — the
``tools/topics_tools.py`` pattern:

  * :func:`classification_build` — the offline few-shot run: fit the
    configured tier (head on stored embeddings, or the opt-in SetFit
    body) against a seed label set, write calibrated per-chunk
    predictions (wholesale write per set), route low confidence to the
    review flag. Honest ``insufficient_labels`` envelopes guard the seed
    set (missing file, one class, thin classes, unresolved ids); a typed
    ``SetfitDependencyError`` surfaces when tier ``setfit`` is configured
    without the ``[setfit]`` extra (HTTP 503 / socket -32005 / exit 3).
  * :func:`classification_list` — the tenant's built predictions. Reads
    are never gated (the ``topics_list`` rule); only the pre-migration
    ``unavailable`` check remains.

Envelope: ``{status, prediction_count, class_count, review_count,
params}`` for the build, ``{status, prediction_count, predictions}`` for
the list — guaranteed on every outcome, so the parity gate holds on the
disabled path too.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import asyncpg
import numpy as np

from ..analysis.settings import load_analysis_settings
from ..classification import engine, predict, seeds, store, tier_a, tier_b
from .ingest_common import DEFAULT_TENANT_ID, load_config_or_pass

__all__ = ["REVIEW_THRESHOLD", "classification_build", "classification_list"]

# Read/list bounds (the ingest_status convention: bounded, honest windows).
DEFAULT_LIST_LIMIT = 50
MAX_LIST_LIMIT = 500

# The review threshold is a code constant; the build records it in params.
REVIEW_THRESHOLD = predict.REVIEW_THRESHOLD

_DISABLED_REASON = "analysis.classification.enabled is false in the loaded config."
_DISABLED_REMEDY = "Set analysis.classification.enabled: true, then retry."
_UNAVAILABLE_REASON = "classification tables absent (migration 029 not applied)"
_UNAVAILABLE_REMEDY = "run corpus-kb setup"


def _disabled_build() -> dict[str, object]:
    """The honest no-op envelope (build keys) for the dormant default."""
    return {
        "status": "disabled",
        "reason": _DISABLED_REASON,
        "remedy": _DISABLED_REMEDY,
        "prediction_count": 0,
        "class_count": 0,
        "review_count": 0,
        "params": {},
    }


def _unavailable_build() -> dict[str, object]:
    """The honest pre-migration envelope (build keys)."""
    return {
        "status": "unavailable",
        "reason": _UNAVAILABLE_REASON,
        "remedy": _UNAVAILABLE_REMEDY,
        "prediction_count": 0,
        "class_count": 0,
        "review_count": 0,
        "params": {},
    }


def _unavailable_list() -> dict[str, object]:
    """The honest pre-migration envelope (list keys)."""
    return {
        "status": "unavailable",
        "reason": _UNAVAILABLE_REASON,
        "remedy": _UNAVAILABLE_REMEDY,
        "prediction_count": 0,
        "predictions": [],
    }


def _insufficient(reason: str) -> dict[str, object]:
    """The honest thin-seed envelope; the remedy names the seed schema."""
    return {
        "status": "insufficient_labels",
        "reason": reason,
        "remedy": seeds.SEED_SCHEMA_REMEDY,
        "prediction_count": 0,
        "class_count": 0,
        "review_count": 0,
        "params": {},
    }


def _seed_params(seed_file: str, vector_count: int) -> dict[str, object]:
    """Build-level params: the seed file identity and the corpus size."""
    raw = Path(seed_file).read_bytes()
    digest = hashlib.sha256(raw).hexdigest()[:12]
    return {
        "seed_file": Path(seed_file).name,
        "seed_file_sha256": digest,
        "vectors_scored": vector_count,
    }


async def classification_build(
    pg_pool: asyncpg.Pool,
    config: dict[str, object] | None = None,
    tenant_id: str = DEFAULT_TENANT_ID,
    seed_file: str | None = None,
) -> dict[str, object]:
    """Fit the classifier against the seed set; write the predictions.

    Returns ``{status, prediction_count, class_count, review_count,
    params}`` (plus reason/remedy on the honest guard paths).
    """
    settings = load_analysis_settings(load_config_or_pass(config))
    tier_settings = settings.classification
    if not tier_settings.enabled:
        return _disabled_build()
    if not await store.classification_tables_present(pg_pool):
        return _unavailable_build()
    if not seed_file:
        return _insufficient("No seed file was given (seed_file is missing).")
    try:
        seed_set = seeds.parse_seed_file(seed_file)
    except ValueError as exc:
        return _insufficient(str(exc))
    if len(seed_set.classes) < 2:
        return _insufficient(
            f"The seed set holds {len(seed_set.classes)} class; classification needs at least 2."
        )
    vectors = await engine.read_stored_vectors(pg_pool, tenant_id)
    try:
        features, labels = seeds.resolve(seed_set, vectors)
    except ValueError as exc:
        return _insufficient(str(exc))
    thin = _thin_classes(seed_set, tier_settings.min_labels_per_class)
    if thin is not None:
        return _insufficient(thin)
    outcome = _fit_and_score(seed_set, vectors, features, labels, tier_settings.tier, seed_file)
    await store.write_build(
        pg_pool,
        tenant_id,
        Path(seed_file).name,
        outcome.classes,
        tier_settings.tier,
        outcome.params,
        outcome.rows,
    )
    return {
        "status": "success",
        "prediction_count": len(outcome.rows),
        "class_count": len(outcome.classes),
        "review_count": sum(1 for row in outcome.rows if row.review),
        "params": outcome.params,
    }


def _thin_classes(seed_set: seeds.SeedSet, floor: int) -> str | None:
    """The per-class counts sentence when a class sits below the floor."""
    counts = seed_set.class_counts
    below = {name: count for name, count in counts.items() if count < floor}
    if not below:
        return None
    detail = ", ".join(f"{name}={count}" for name, count in counts.items())
    return (
        f"Every class needs at least {floor} seeds (SetFit paper scope starts at "
        f"{floor}). Per-class seed counts: {detail}."
    )


def _fit_and_score(
    seed_set: seeds.SeedSet,
    vectors: list[engine.StoredVector],
    features: np.ndarray,
    labels: np.ndarray,
    tier: str,
    seed_file: str,
) -> predict.BuildOutcome:
    """Route the tier, fit, score the corpus, calibrate, flag review."""
    base_params = _seed_params(seed_file, len(vectors))
    if tier == "setfit":
        train_texts = predict.seed_texts(seed_set, vectors)
        fitter = tier_b.fit_setfit(train_texts, [str(label) for label in labels])
        corpus_texts = [vector.text for vector in vectors]
        return predict.build_predictions(
            fitter,
            fitter.embed(train_texts),
            labels,
            vectors,
            fitter.embed(corpus_texts),
            base_params,
        )
    fitted = tier_a.fit_head(features, labels)
    corpus = np.stack([vector.vector for vector in vectors]).astype(np.float32)
    return predict.build_predictions(fitted, features, labels, vectors, corpus, base_params)


async def classification_list(
    pg_pool: asyncpg.Pool,
    config: dict[str, object] | None = None,
    tenant_id: str = DEFAULT_TENANT_ID,
    limit: int = DEFAULT_LIST_LIMIT,
) -> dict[str, object]:
    """The tenant's built predictions, deterministic order (bounded window).

    Reads are never gated: inspecting an existing build while the feature
    flag is off is safe (the ``topics_list`` rule).
    """
    del config  # accepted for MCP-tool signature symmetry; reads are config-free
    bounded = max(1, min(int(limit), MAX_LIST_LIMIT))
    if not await store.classification_tables_present(pg_pool):
        return _unavailable_list()
    rows = await store.read_predictions(pg_pool, tenant_id, bounded)
    return {"status": "success", "prediction_count": len(rows), "predictions": rows}
