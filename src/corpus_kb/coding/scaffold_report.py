"""Scaffold report reads: one tenant-scoped pass, keyword precision, gaps.

The DB-facing half of the T0 scaffold (TD-11/T0-3). ``build_scaffold_report``
fetches stored state through :mod:`corpus_kb.coding.report_reads`, computes
the MECE math in :mod:`corpus_kb.coding.mece_report`, and returns the
``mece`` and ``keyword_precision`` envelope payloads for the coding_pool
op. Stores without the coding tables degrade to ``status: unavailable``
blocks, never a crash.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from corpus_kb.coding._scope import CodingStore
from corpus_kb.coding.mece_report import mece_block
from corpus_kb.coding.report_reads import (
    coverage_gaps_block,
    fetch_inputs,
    keyword_precision_block,
)

__all__ = ["build_scaffold_report"]


async def build_scaffold_report(
    pool: CodingStore, tenant_id: UUID, codebook_version_id: UUID
) -> dict[str, Any]:
    """One report dict: the ``mece`` and ``keyword_precision`` payloads."""
    mece: dict[str, Any]
    try:
        inputs = await fetch_inputs(pool, tenant_id, codebook_version_id)
        mece = {"status": "success", **mece_block(inputs)}
        precision = await keyword_precision_block(pool, tenant_id)
        gaps = await coverage_gaps_block(pool, tenant_id, codebook_version_id)
        mece["coverage_gaps"] = gaps
    except Exception:
        reason = "coding tables absent (run corpus-kb setup)"
        mece = {
            "status": "unavailable",
            "reason": reason,
            "coverage_gaps": {"status": "unavailable", "reason": reason},
        }
        precision = {"status": "unavailable", "reason": reason}
    return {"mece": mece, "keyword_precision": precision}
