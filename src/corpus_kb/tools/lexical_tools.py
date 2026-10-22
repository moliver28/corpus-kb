"""MCP/HTTP/socket/CLI handler for the lexical classify op (AMR program).

One handler per operation, shared by every surface — the
``tools/topics_tools.py`` pattern. :func:`lexical_classify` trains a
TF-IDF + logistic head on the tenant's STORED weak-supervision labels
(the ``chunk_labels`` build, migration 028) and scores every stored
chunk in one bounded window.

LAZY DEPENDENCIES (the P0 rule): sklearn comes in through the tier-A
seam (``ensure_sklearn`` / ``thread_pin``), function-local only, with
every fit and score pass pinned to one BLAS thread (the bitwise
determinism contract). A missing ``[classify]`` extra raises the typed
``HeadDependencyError``.

Honest outcomes: ``unavailable`` (migration 028 not applied) and
``empty`` (no stored labels, one class, a class under the per-class
floor, or no label resolvable to a stored chunk) — each with a reason
and one remedy. Predictions come back chunk-id ascending; floats are
rounded to 6 places.
"""

from __future__ import annotations

import asyncpg

from ..classification.tier_a import ensure_sklearn, thread_pin
from ..labeling import engine, writer
from .ingest_common import DEFAULT_TENANT_ID

__all__ = ["MIN_CLASS_ROWS", "lexical_classify"]

# The per-class floor: one example per class cannot teach a logistic
# head anything a coin flip does not already know.
MIN_CLASS_ROWS = 2

# Working-set bounds (the engine's keyset reads take these caps).
MAX_LABELED_WINDOW = 50_000
MAX_SCREEN_WINDOW = 50_000

_UNAVAILABLE_REASON = "labeling tables absent (migration 028 not applied)"
_UNAVAILABLE_REMEDY = "run corpus-kb setup"

_ROUND = 6


def _empty(reason: str, remedy: str) -> dict[str, object]:
    """The honest no-op envelope on the op keys (reason + remedy named)."""
    return {
        "status": "empty",
        "reason": reason,
        "remedy": remedy,
        "predictions": [],
        "screened_count": 0,
    }


async def lexical_classify(
    pg_pool: asyncpg.Pool,
    config: dict[str, object] | None = None,
    tenant_id: str = DEFAULT_TENANT_ID,
    chunk_ids: list[str] | None = None,
) -> dict[str, object]:
    """Train on the stored labels and score the corpus (one window).

    Returns ``{status, predictions, screened_count}`` (rows: chunk_id,
    label, probability) plus a params record naming the classes, the
    training rows and the model. ``chunk_ids`` narrows the scored
    corpus; training always uses every stored label that resolves.
    """
    del config  # accepted for MCP-tool signature symmetry; reads are config-free
    # The typed dependency gate runs before any probing (the
    # classification_build order): an extras-less install answers the
    # honest typed envelope, never a mid-fit ModuleNotFoundError.
    ensure_sklearn()
    if not await writer.labeling_tables_present(pg_pool):
        return {
            "status": "unavailable",
            "reason": _UNAVAILABLE_REASON,
            "remedy": _UNAVAILABLE_REMEDY,
            "predictions": [],
            "screened_count": 0,
        }
    label_rows = await writer.read_labels(pg_pool, tenant_id, MAX_LABELED_WINDOW)
    if not label_rows:
        return _empty(
            "the tenant holds no built chunk labels.",
            "Run labeling-build with an LF file first, then retry.",
        )
    chunks = await engine.read_chunks(pg_pool, tenant_id, MAX_SCREEN_WINDOW)
    texts = {
        chunk.chunk_id: chunk.text
        for chunk in chunks
        if chunk_ids is None or chunk.chunk_id in set(chunk_ids)
    }
    labeled = [
        (str(row["chunk_id"]), str(row["label"]), texts[str(row["chunk_id"])])
        for row in label_rows
        if str(row["chunk_id"]) in texts
    ]
    if not labeled:
        return _empty(
            "no stored label resolves to a stored chunk in this corpus window.",
            "Rebuild labels over the ingested corpus (labeling-build), then retry.",
        )
    classes = sorted({label for _cid, label, _text in labeled})
    if len(classes) < 2:
        return _empty(
            f"the stored labels hold {len(classes)} class; a classifier needs at least two.",
            "Broaden the LF file so the build produces two or more classes, then retry.",
        )
    counts = dict.fromkeys(classes, 0)
    for _cid, label, _text in labeled:
        counts[label] += 1
    thin = sorted(name for name, count in counts.items() if count < MIN_CLASS_ROWS)
    if thin:
        detail = ", ".join(f"{name}={counts[name]}" for name in classes)
        return _empty(
            f"every class needs at least {MIN_CLASS_ROWS} labeled chunks. Per-class "
            f"counts: {detail}.",
            "Add labeling functions or chunks so each class clears the floor, then retry.",
        )
    predictions = _fit_and_score(labeled, classes, list(texts.items()))
    predictions.sort(key=lambda row: str(row["chunk_id"]))
    return {
        "status": "success",
        "predictions": predictions,
        "screened_count": len(predictions),
        "params": {
            "model": "tfidf+logistic",
            "classes": classes,
            "labeled_rows": len(labeled),
            "chunks_scored": len(predictions),
            "min_class_rows": MIN_CLASS_ROWS,
        },
    }


def _fit_and_score(
    labeled: list[tuple[str, str, str]],
    classes: list[str],
    corpus: list[tuple[str, str]],
) -> list[dict[str, object]]:
    """TF-IDF fit on the labeled rows, logistic fit, corpus scores (pinned)."""
    from sklearn.feature_extraction.text import TfidfVectorizer

    from ..classification import tier_a

    train_texts = [text for _cid, _label, text in labeled]
    train_labels = [label for _cid, label, _text in labeled]
    corpus_texts = [text for _cid, text in corpus]
    with thread_pin():
        vectorizer = TfidfVectorizer()
        features = vectorizer.fit_transform(train_texts)
        head = tier_a.head_estimator()
        head.fit(features, train_labels)
        scored = vectorizer.transform(corpus_texts)
        probabilities = head.predict_proba(scored)
    order = {name: index for index, name in enumerate(head.classes_)}
    predictions: list[dict[str, object]] = []
    for (chunk_id, _text), row in zip(corpus, probabilities, strict=True):
        best = max(range(len(row)), key=lambda position: (float(row[position]), -position))
        label = str(head.classes_[best])
        predictions.append(
            {
                "chunk_id": chunk_id,
                "label": label,
                "probability": round(float(row[order[label]]), _ROUND),
            }
        )
    return predictions
