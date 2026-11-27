"""The owner-facing scaffold report (T0-6, R16): numbers plus plain sentences.

``build_scaffold_report`` reads the stored state through
:mod:`corpus_kb.coding.report_reads`, computes the MECE math in
:mod:`corpus_kb.coding.mece_report`, and returns the ``mece``,
``keyword_precision``, and ``scaffold`` envelope payloads for the
coding_pool op. The ``scaffold`` block states the numbers in short plain
sentences (ASD-STE100), the seed count against the grade-A bar (TD-07
point 4), and a sample of the step ledger, so the owner can give the T1
go from this key alone (TD-02). Stores without the coding tables degrade
to ``status: unavailable`` blocks, never a crash.
"""

from __future__ import annotations

import logging
from typing import Any
from uuid import UUID

from corpus_kb.coding._scope import CodingStore, missing_table_error, tenant_scope
from corpus_kb.coding.bench_seeds import packaged_seed_set
from corpus_kb.coding.mece_report import mece_block
from corpus_kb.coding.report_reads import (
    coverage_gaps_block,
    fetch_inputs,
    keyword_precision_block,
)
from corpus_kb.errors import SeedFileError

logger = logging.getLogger(__name__)

__all__ = ["build_scaffold_report"]

#: TD-07 point 4: at 0.05/0.95, grade A needs at least 59 error-free
#: held-out seeds (Clopper-Pearson, one-sided). The scaffold report
#: states the current seed count against this bar.
GRADE_A_SEED_BAR = 59


def _pct(block: dict[str, Any]) -> str:
    """One reported share as a short percent string."""
    value = block.get("value")
    return f"{(100 * float(value)):.1f}%" if isinstance(value, (int, float)) else "not measured"


def _scaffold_lines(mece: dict[str, Any], precision: dict[str, Any]) -> list[str]:
    """The owner-readable report lines (plain English, ASD-STE100)."""
    lines: list[str] = []
    if mece.get("status") != "success":
        lines.append(str(mece.get("reason", "The report could not read the store.")))
        return lines
    ex = mece.get("exclusivity", {})
    exh = mece.get("exhaustiveness", {})
    lines.append(
        f"Embedder: {mece.get('embedder', 'unknown')}. Chunks measured: {mece.get('n_chunks', 0)}."
    )
    auto = _pct(ex.get("multi_automatic_code_share", {}))
    lines.append(f"Chunks with two or more automatic codes: {auto}.")
    kw = _pct(ex.get("multi_keyword_code_share", {}))
    lines.append(f"Chunks with two or more keyword codes: {kw}.")
    lines.append(f"Chunks with at least one code: {_pct(exh.get('coded_share', {}))}.")
    lines.append(f"Residue chunks (no code): {exh.get('residue_size', 0)}.")
    stability = exh.get("stability", {})
    ari = stability.get("mean_pairwise_ari")
    note = "not enough residue chunks." if ari is None else f"{float(ari):.3f}."
    lines.append(f"Residue cluster stability (bootstrap ARI): {note}")
    lines.append(f"Keywords tracked: {precision.get('n_keywords', 0)}.")
    lines.append("Keyword precision needs graded seeds; none is graded yet.")
    gaps = (mece.get("coverage_gaps") or {}).get("per_code", [])
    for row in gaps[:5]:
        lines.append(
            f"Code {row['code_id']}: {_pct(row['missing_share'])} of chunks miss a tier-0 row."
        )
    return lines


async def _ledger_sample(pool: CodingStore, tenant_id: UUID) -> list[dict[str, Any]]:
    """The newest ledger rows as a small sample; empty when the table is absent."""
    try:
        async with tenant_scope(pool, tenant_id) as conn:
            rows = await conn.fetch(
                "SELECT operation, kind, status, started_at FROM analysis_steps "
                "WHERE tenant_id=$1 ORDER BY started_at DESC, step_id DESC LIMIT 5",
                tenant_id,
            )
    except Exception as exc:
        if not missing_table_error(exc):
            raise
        logger.warning("Ledger sample degraded: the analysis_steps table is absent", exc_info=True)
        return []
    return [
        {
            "operation": str(r["operation"]),
            "kind": str(r["kind"]),
            "status": str(r["status"]),
            "started_at": str(r["started_at"]),
        }
        for r in rows
    ]


def _seed_bar_block() -> dict[str, Any]:
    """The packaged seed count against the grade-A bar (TD-07 point 4)."""
    try:
        seeds = packaged_seed_set()
        count = len(seeds)
        types = sorted({s.error_type for s in seeds})
    except (OSError, SeedFileError):
        logger.warning("Packaged seed set is unreadable; the seed bar degrades", exc_info=True)
        count, types = 0, []
    return {
        "grade_a_bar": GRADE_A_SEED_BAR,
        "packaged_seed_rows": count,
        "packaged_error_types": types,
        "note": (
            f"Grade A needs {GRADE_A_SEED_BAR} error-free held-out seeds. "
            "The packaged set holds planted errors, not calibration seeds."
        ),
    }


async def build_scaffold_report(
    pool: CodingStore, tenant_id: UUID, codebook_version_id: UUID
) -> dict[str, Any]:
    """One report dict: the ``mece``, ``keyword_precision``, and ``scaffold`` payloads."""
    mece: dict[str, Any]
    try:
        inputs = await fetch_inputs(pool, tenant_id, codebook_version_id)
        mece = {"status": "success", **mece_block(inputs)}
        precision = await keyword_precision_block(pool, tenant_id)
        gaps = await coverage_gaps_block(pool, tenant_id, codebook_version_id)
        mece["coverage_gaps"] = gaps
    except Exception as exc:
        if not missing_table_error(exc):
            raise
        logger.warning("Scaffold report degraded: the coding tables are absent", exc_info=True)
        reason = "coding tables absent (run corpus-kb setup)"
        mece = {
            "status": "unavailable",
            "reason": reason,
            "coverage_gaps": {"status": "unavailable", "reason": reason},
        }
        precision = {"status": "unavailable", "reason": reason}
    scaffold = {
        "lines": _scaffold_lines(mece, precision),
        "seed_bar": _seed_bar_block(),
        "ledger_sample": await _ledger_sample(pool, tenant_id),
        "mece": mece,
        "keyword_precision": precision,
    }
    return {"mece": mece, "keyword_precision": precision, "scaffold": scaffold}
