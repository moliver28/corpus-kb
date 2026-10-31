"""The EECS math: confusion matrix and per-class precision/recall (pure).

The error-analysis report section (``eecs`` in ``analyze_run``) compares
stored ``chunk_predictions`` against gold ``chunk_labels`` through these
helpers. Both are pure functions with one determinism contract: labels
sort ascending unless the caller passes an explicit order, matrix rows
are true labels and columns are predicted labels, and every float is
rounded to 6 places (the byte-determinism convention). A class with no
true positives and no predictions scores 0.0 — never NaN, never a guess.

The Postgres readers for the paired rows live at the bottom of this
module (clearly separated from the math); the section composer in
``tools/analysis_tools.py`` is their only consumer.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Sequence
from typing import cast

import asyncpg

from ..analysis import report as report_mod
from ..analysis.settings import AnalysisSettings
from ..classification import store as classification_store
from ..labeling import writer
from ..storage.tenant_conn import tenant_connection

__all__ = [
    "confusion_matrix",
    "eecs_section",
    "evaluate",
    "per_class_precision_recall",
    "read_paired_rows",
]

_ROUND = 6


def _resolve_labels(
    y_true: Sequence[str], y_pred: Sequence[str], labels: Sequence[str] | None
) -> list[str]:
    """The label order: explicit when given, else the sorted union.

    The explicit order IS the caller's vocabulary; a label no row
    reached keeps its all-zero row (an absent class is a result, not an
    error).
    """
    if labels is not None:
        return list(labels)
    return sorted(set(y_true) | set(y_pred))


def _validate_pairs(y_true: Sequence[str], y_pred: Sequence[str]) -> None:
    """The shared input guard: aligned lengths, both non-empty."""
    if len(y_true) != len(y_pred):
        raise ValueError("y_true and y_pred must align (one prediction per truth row)")
    if not y_true:
        raise ValueError("y_true and y_pred must be non-empty")


def confusion_matrix(
    y_true: Sequence[str],
    y_pred: Sequence[str],
    labels: Sequence[str] | None = None,
) -> dict[str, object]:
    """Cell counts against the label order; rows are truth, columns predictions.

    Returns ``{"labels": [...], "matrix": [[int]]}``. Mismatched input
    lengths or empty inputs raise ``ValueError`` — a silent partial
    matrix would understate the error counts.
    """
    _validate_pairs(y_true, y_pred)
    order = _resolve_labels(y_true, y_pred, labels)
    index = {label: position for position, label in enumerate(order)}
    matrix = [[0 for _ in order] for _ in order]
    for truth, predicted in zip(y_true, y_pred, strict=True):
        matrix[index[truth]][index[predicted]] += 1
    return {"labels": order, "matrix": matrix}


def per_class_precision_recall(
    y_true: Sequence[str],
    y_pred: Sequence[str],
    labels: Sequence[str] | None = None,
) -> list[dict[str, object]]:
    """One metric row per label, in the label order.

    Rows carry ``label, tp, fp, fn, support, precision, recall, f1``.
    A denominator of zero scores 0.0 (the scikit-learn zero-division
    convention), so an absent class is an honest zero, never NaN.
    """
    _validate_pairs(y_true, y_pred)
    order = _resolve_labels(y_true, y_pred, labels)
    rows: list[dict[str, object]] = []
    for label in order:
        pairs = list(zip(y_true, y_pred, strict=True))
        tp = sum(1 for truth, pred in pairs if truth == label and pred == label)
        fp = sum(1 for truth, pred in pairs if truth != label and pred == label)
        fn = sum(1 for truth, pred in pairs if truth == label and pred != label)
        precision = tp / (tp + fp) if (tp + fp) > 0 else 0.0
        recall = tp / (tp + fn) if (tp + fn) > 0 else 0.0
        f1 = 2 * precision * recall / (precision + recall) if (precision + recall) > 0 else 0.0
        rows.append(
            {
                "label": label,
                "tp": tp,
                "fp": fp,
                "fn": fn,
                "support": tp + fn,
                "precision": round(precision, _ROUND),
                "recall": round(recall, _ROUND),
                "f1": round(f1, _ROUND),
            }
        )
    return rows


def evaluate(y_true: Sequence[str], y_pred: Sequence[str]) -> dict[str, object]:
    """The EECS body in one call: labels, matrix, per-class rows."""
    matrix = confusion_matrix(y_true, y_pred)
    labels = cast("list[str]", matrix["labels"])
    return {
        "labels": labels,
        "matrix": matrix["matrix"],
        "per_class": per_class_precision_recall(y_true, y_pred, labels=labels),
    }


# --- The EECS data path: paired stored predictions vs gold labels ----------

# Working-set bound for both reads (a guard, not a target).
MAX_PAIRED_WINDOW = 100_000


async def read_paired_rows(
    pg_pool: asyncpg.Pool, tenant_id: str, cap: int = MAX_PAIRED_WINDOW
) -> tuple[list[tuple[str, str, str]], int, int, str | None]:
    """Pair the newest prediction set with the gold chunk labels.

    Returns ``(paired, gold_count, prediction_count, set_id)`` where each
    paired row is ``(chunk_id, gold, predicted)`` in chunk-id order.
    The evaluated set is the newest ``label_sets`` row (created_at desc,
    set_id tiebreak) — one set per report, deterministically chosen.
    """
    async with tenant_connection(pg_pool, tenant_id) as conn:
        set_rows = await conn.fetch(
            """
            SELECT set_id::text AS set_id FROM label_sets
            WHERE tenant_id = $1
            ORDER BY created_at DESC, set_id
            LIMIT 1
            """,
            tenant_id,
        )
        if not set_rows:
            return [], 0, 0, None
        set_id = str(set_rows[0]["set_id"])
        predictions = {
            str(row["chunk_id"]): str(row["label"])
            for row in await conn.fetch(
                """
                SELECT chunk_id::text AS chunk_id, label FROM chunk_predictions
                WHERE tenant_id = $1 AND set_id = $2::uuid
                ORDER BY chunk_id
                LIMIT $3
                """,
                tenant_id,
                set_id,
                cap,
            )
        }
        gold = {
            str(row["chunk_id"]): str(row["label"])
            for row in await conn.fetch(
                """
                SELECT chunk_id::text AS chunk_id, label FROM chunk_labels
                WHERE tenant_id = $1
                ORDER BY chunk_id
                LIMIT $2
                """,
                tenant_id,
                cap,
            )
        }
    paired = [
        (chunk_id, gold[chunk_id], predictions[chunk_id])
        for chunk_id in sorted(set(gold) & set(predictions))
    ]
    return paired, len(gold), len(predictions), set_id


async def eecs_section(
    pg_pool: asyncpg.Pool,
    config: dict[str, object] | None,
    tenant_id: str,
    settings: AnalysisSettings | None = None,
) -> report_mod.AnalysisSection:
    """The ``eecs`` report section: error counts over the paired rows.

    The caller gates on ``analysis.eecs.enabled``. Both table families
    (migrations 028 and 029) must exist; with no overlap between stored
    predictions and gold labels the section is honestly skipped. A
    failing read becomes a ``failed`` section (the report house rule).
    """
    del config, settings  # the gate ran in analyze_run; the section reads only
    if not await classification_store.classification_tables_present(pg_pool) or not (
        await writer.labeling_tables_present(pg_pool)
    ):
        return report_mod.AnalysisSection(
            name="eecs",
            status="unavailable",
            detail={
                "reason": "the eecs section needs migrations 028 and 029 (labeling and "
                "classification tables).",
                "remedy": "Run corpus-kb setup, then retry.",
            },
        )
    return await report_mod.run_section("eecs", _run_eecs(pg_pool, tenant_id))


def _run_eecs(pg_pool: asyncpg.Pool, tenant_id: str) -> Callable[[], Awaitable[dict[str, object]]]:
    """The section runner: pair, evaluate, count the honest denominators."""

    async def run() -> dict[str, object]:
        paired, gold_count, prediction_count, set_id = await read_paired_rows(pg_pool, tenant_id)
        if not paired:
            return {
                "status": "skipped",
                "reason": "no chunk holds both a stored prediction and a gold label.",
                "remedy": (
                    "Build labels (labeling-build) and predictions (classification-build) "
                    "over the same corpus, then retry."
                ),
                "gold_count": gold_count,
                "prediction_count": prediction_count,
            }
        truth = [row[1] for row in paired]
        predicted = [row[2] for row in paired]
        return {
            "status": "success",
            **evaluate(truth, predicted),
            "paired_count": len(paired),
            "gold_count": gold_count,
            "prediction_count": prediction_count,
            "set_id": set_id,
        }

    return run
