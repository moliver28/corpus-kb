"""MCP/HTTP/socket/CLI handlers for the labeling ops (WP-2, analysis program).

One handler per operation, shared by every surface — the
``tools/topics_tools.py`` pattern:

  * :func:`labeling_build` — the offline weak-supervision run: load the
    LF file, score it over the tenant's chunks, aggregate with the
    existing Dawid-Skene code, write the labels wholesale.
  * :func:`labeling_list` — the tenant's built chunk labels. Reads are
    never gated (the ``topics_list`` rule); only the tables check.
  * :func:`labeling_lfs` — the tenant's registered LFs, each row with
    per-LF stats over a bounded chunk window. Reads are never gated.

Envelope: ``{status, label_count, labels}`` for the build/list pair,
``{status, lf_count, lfs}`` for the LF registry read — guaranteed on
every outcome, so the parity gate holds on every path. Success extras
ride beyond the tuple (the topics_build precedent): chunk_count,
review_count, abstain_count, lf_stats, warnings, params.

Honest outcomes: ``disabled`` (``analysis.labeling.enabled`` false —
build only), ``unavailable`` (migration 028 not applied),
``insufficient_data`` (no LF file, file not found, zero LFs, zero
chunks). A malformed LF file raises ``ValueError`` with the fix in the
message — a broken input never becomes a quiet empty run.
"""

from __future__ import annotations

from pathlib import Path

import asyncpg

from ..analysis.settings import load_analysis_settings
from ..coding.aggregation import MISSING, WeakSupervisionResult, aggregate_signals
from ..labeling import analysis as lf_analysis
from ..labeling import engine, writer
from ..labeling.lfs import LFSpec, load_lfs
from .ingest_common import DEFAULT_TENANT_ID, load_config_or_pass

__all__ = ["labeling_build", "labeling_lfs", "labeling_list"]

# Read/list bounds (the ingest_status convention: bounded, honest windows).
DEFAULT_LIST_LIMIT = 50
MAX_LIST_LIMIT = 500

# The success-envelope sample and the registry-read stats window.
LABEL_SAMPLE = 50
LFS_STATS_CHUNKS = 2000

_DISABLED_REASON = "analysis.labeling.enabled is false in the loaded config."
_DISABLED_REMEDY = "Set analysis.labeling.enabled: true, then retry."
_UNAVAILABLE_REASON = "labeling tables absent (migration 028 not applied)"
_UNAVAILABLE_REMEDY = "run corpus-kb setup"


def _labels_honest(status: str, reason: str, remedy: str) -> dict[str, object]:
    """An honest no-op envelope on the label keys (reason + remedy named)."""
    return {
        "status": status,
        "reason": reason,
        "remedy": remedy,
        "label_count": 0,
        "labels": [],
    }


def _lfs_honest(status: str, reason: str, remedy: str) -> dict[str, object]:
    """An honest no-op envelope on the LF keys (reason + remedy named)."""
    return {
        "status": status,
        "reason": reason,
        "remedy": remedy,
        "lf_count": 0,
        "lfs": [],
    }


def _lfs_file_gate(lfs_file: str | None) -> tuple[str, str] | None:
    """(reason, remedy) when no LF file is usable; None when one exists."""
    if not lfs_file:
        return (
            "analysis.labeling.lfs_file is not set.",
            "Point analysis.labeling.lfs_file at the labeling-functions JSON file.",
        )
    if not Path(lfs_file).is_file():
        return (
            f"the labeling-function file was not found: {lfs_file}.",
            "Create the file, or fix analysis.labeling.lfs_file.",
        )
    return None


async def labeling_build(
    pg_pool: asyncpg.Pool,
    config: dict[str, object] | None = None,
    tenant_id: str = DEFAULT_TENANT_ID,
) -> dict[str, object]:
    """Score the labeling functions and write the aggregated labels.

    Returns ``{status, label_count, labels}`` plus the counts, per-LF
    stats, F6 warnings and the params record on success. Abstained
    chunks get no row (non-membership is honest); reviewed chunks keep
    their label and the review flag in the stored detail.
    """
    settings = load_analysis_settings(load_config_or_pass(config))
    if not settings.labeling.enabled:
        return _labels_honest("disabled", _DISABLED_REASON, _DISABLED_REMEDY)
    if not await writer.labeling_tables_present(pg_pool):
        return _labels_honest("unavailable", _UNAVAILABLE_REASON, _UNAVAILABLE_REMEDY)
    gate = _lfs_file_gate(settings.labeling.lfs_file)
    if gate is not None:
        return _labels_honest("insufficient_data", gate[0], gate[1])
    spec = load_lfs(settings.labeling.lfs_file or "")
    if not spec.lfs:
        return _labels_honest(
            "insufficient_data",
            "the labeling-function file defines zero labeling functions.",
            "Add at least one labeling function to the file, then retry.",
        )

    scored = await engine.score_lfs(pg_pool, tenant_id, spec)
    if scored.chunk_count == 0:
        return _labels_honest(
            "insufficient_data",
            "the tenant has no chunks to label.",
            "Ingest documents, then retry.",
        )
    agg = aggregate_signals(
        scored.matrix,
        n_classes=len(spec.classes),
        min_signals=settings.labeling.min_signals,
    )
    rows = _label_rows(scored, agg, spec)
    written = await writer.write_build(pg_pool, tenant_id, spec, rows)
    pairs = lf_analysis.pairwise_agreements(scored.matrix, spec)
    return {
        "status": "success",
        "label_count": written["labels_written"],
        "labels": [
            {"chunk_id": row.chunk_id, "label": row.label, "probability": row.probability}
            for row in rows[:LABEL_SAMPLE]
        ],
        "chunk_count": scored.chunk_count,
        "review_count": int(agg.review.sum()),
        "abstain_count": int(agg.abstain.sum()),
        "lf_stats": [stat.to_dict() for stat in lf_analysis.lf_stats(scored.matrix, spec)],
        "warnings": lf_analysis.agreement_warnings(pairs),
        "params": {
            "lfs_file": settings.labeling.lfs_file,
            "classes": list(spec.classes),
            "lf_count": len(spec.lfs),
            "min_signals": settings.labeling.min_signals,
            "chunks_scanned": scored.chunk_count,
            "aggregation": agg.status,
            "aggregation_detail": agg.detail,
        },
    }


def _label_rows(
    scored: engine.ScoredCorpus, agg: WeakSupervisionResult, spec: LFSpec
) -> list[writer.LabelRow]:
    """Aggregate outcome -> storage rows (abstained chunks get no row)."""
    rows: list[writer.LabelRow] = []
    for position, chunk_id in enumerate(scored.chunk_ids):
        if bool(agg.abstain[position]):
            continue
        fires = {
            scored.lf_names[column]: spec.classes[int(scored.matrix[position, column])]
            for column in range(len(scored.lf_names))
            if scored.matrix[position, column] != MISSING
        }
        label_index = int(agg.labels[position])
        rows.append(
            writer.LabelRow(
                chunk_id=chunk_id,
                label=spec.classes[label_index],
                probability=float(agg.probabilities[position, label_index]),
                detail={
                    "lf_fires": fires,
                    "n_signals": len(fires),
                    "review": bool(agg.review[position]),
                },
            )
        )
    return rows


async def labeling_list(
    pg_pool: asyncpg.Pool,
    config: dict[str, object] | None = None,
    tenant_id: str = DEFAULT_TENANT_ID,
    limit: int = DEFAULT_LIST_LIMIT,
) -> dict[str, object]:
    """The tenant's built chunk labels, chunk-id order (bounded window).

    Reads are never gated: inspecting an existing build while the flag
    is off is safe.
    """
    del config  # accepted for MCP-tool signature symmetry; reads are config-free
    bounded = max(1, min(int(limit), MAX_LIST_LIMIT))
    if not await writer.labeling_tables_present(pg_pool):
        return _labels_honest("unavailable", _UNAVAILABLE_REASON, _UNAVAILABLE_REMEDY)
    rows = await writer.read_labels(pg_pool, tenant_id, bounded)
    return {"status": "success", "label_count": len(rows), "labels": rows}


async def labeling_lfs(
    pg_pool: asyncpg.Pool,
    config: dict[str, object] | None = None,
    tenant_id: str = DEFAULT_TENANT_ID,
    limit: int = DEFAULT_LIST_LIMIT,
) -> dict[str, object]:
    """The tenant's registered LFs with per-LF stats, name order.

    Stats are computed over the first ``LFS_STATS_CHUNKS`` chunks by
    chunk id (the window size rides each row's stats). Reads are never
    gated.
    """
    del config  # accepted for MCP-tool signature symmetry; reads are config-free
    bounded = max(1, min(int(limit), MAX_LIST_LIMIT))
    if not await writer.labeling_tables_present(pg_pool):
        return _lfs_honest("unavailable", _UNAVAILABLE_REASON, _UNAVAILABLE_REMEDY)
    lf_rows = await writer.read_lfs(pg_pool, tenant_id, bounded)
    stats = await engine.live_stats(pg_pool, tenant_id, lf_rows, LFS_STATS_CHUNKS)
    rows = [{**row, "stats": stats.get(str(row["name"]))} for row in lf_rows]
    return {"status": "success", "lf_count": len(rows), "lfs": rows}
