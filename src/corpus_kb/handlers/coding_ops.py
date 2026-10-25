"""Coding operation bodies (pure move out of ``coding_handler.py``).

``code_batch`` / ``pool_summaries`` / ``embed_codebook`` / ``embed_text``
are the byte-identical ``CodingHandler`` method bodies;
``build_reliability_report`` is the computation half of
``handle_reliability`` (the tenant-scoped fetches stay in the method so
they keep routing through the handler's patched ``tenant_scope``).
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import Any
from uuid import UUID

from corpus_kb.coding._scope import CodingStore
from corpus_kb.coding.codebook_loader import CodebookEmbeddingError, load_codebook
from corpus_kb.coding.coder_dispatch import dispatch_chunk
from corpus_kb.coding.confidence_routing import Bands
from corpus_kb.coding.judgment_sampling import judgment_mean_enabled
from corpus_kb.coding.keyword_governance_report import compute_keyword_governance
from corpus_kb.coding.pooling import (
    materialize_chunk_keyword_hits,
    reconcile_coverage,
    run_similarity_pooling,
)
from corpus_kb.coding.reliability import computeReliability
from corpus_kb.coding.reliability_sampling import insufficient_codes, stratified_sample
from corpus_kb.coding.speaker_role_projection import classify_speaker_roles
from corpus_kb.rag.embedder import OllamaEmbedder, PgmlEmbedder

logger = logging.getLogger(__name__)

# final_codes.status -> the disposition vocabulary reliability_sampling strata on.
_DISPOSITION = {
    "accepted": "accepted",
    "rejected": "rejected",
    "manual_review": "no_code_applies",
}


async def pool_summaries(
    pool: CodingStore, tenant_id: UUID, codebook_version_id: UUID
) -> dict[str, Any]:
    """Run keyword-hit materialization, similarity pooling, and coverage reconciliation.

    Also runs keyword governance (collision/over-cap/keyness) over the
    keyword hits this pass just materialized, surfaced under
    `keyword_governance`; speaker-role classification runs after
    reconcile_coverage backfills chunk_status.
    """
    keywords = await materialize_chunk_keyword_hits(pool, tenant_id, codebook_version_id)
    similarity = await run_similarity_pooling(pool, tenant_id, codebook_version_id)
    coverage = await reconcile_coverage(pool, tenant_id)
    governance = await compute_keyword_governance(pool, tenant_id, codebook_version_id)
    speaker_roles = await classify_speaker_roles(pool, tenant_id)
    return {
        "status": "success",
        "keyword_hits_written": keywords["keyword_hits_written"],
        "chunk_signals_written": similarity["chunk_signals_written"],
        "coverage": coverage,
        "keyword_governance": governance,
        "speaker_roles": speaker_roles,
    }


async def code_batch(
    pool: CodingStore,
    tenant_id: UUID,
    cells: list[dict[str, Any]],
    coder: str,
    model: str,
    call_fn: Callable[[str], Awaitable[dict[str, Any]]] | None,
    batch_id: str | None,
    coding_pass: int,
    bands: Bands | None,
) -> dict[str, Any]:
    """Code a batch of cells.

    `cells` is a list of {"chunk_id": str|UUID, "code_ids": [str, ...]}, one
    entry per dispatch call (<=5 candidate codes each); the response carries
    the per-cell results plus batch counters. A cell that raises (bad
    chunk_id, transport failure, DB error) is recorded as a per-cell error
    and counted as rejected; the batch keeps going.
    """
    results: list[dict[str, Any]] = []
    cells_coded = 0
    cells_rejected = 0
    rows_written = 0
    # Resolve the U35 flag once per batch: judgment_mean_enabled() reads
    # the config from disk, which must not happen per coded cell (hot
    # path latency, and a mid-batch config edit would flip behavior
    # halfway through the run).
    batch_use_judgment_mean = judgment_mean_enabled()
    for cell in cells:
        raw_chunk_id = cell.get("chunk_id")
        try:
            chunk_id = raw_chunk_id if isinstance(raw_chunk_id, UUID) else UUID(str(raw_chunk_id))
            result = await dispatch_chunk(
                pool,
                tenant_id,
                chunk_id,
                list(cell.get("code_ids", [])),
                coder=coder,
                model=model,
                call_fn=call_fn,
                batch_id=batch_id,
                coding_pass=coding_pass,
                bands=bands,
                use_judgment_mean=batch_use_judgment_mean,
            )
        except Exception as exc:  # one bad cell must not abort the batch
            logger.warning("Coding cell %s failed: %s", raw_chunk_id, exc)
            result = {
                "status": "error",
                "code_ids": list(cell.get("code_ids", [])),
                "error": f"{type(exc).__name__}: {exc}",
                "written": 0,
            }
        if result["status"] == "success":
            cells_coded += 1
            rows_written += result["written"]
        else:
            cells_rejected += 1
        result["chunk_id"] = str(raw_chunk_id)
        results.append(result)
    return {
        "status": "success",
        "cells_coded": cells_coded,
        "cells_rejected": cells_rejected,
        "cells_routed": rows_written,
        "results": results,
    }


async def build_reliability_report(
    rows: list[dict[str, Any]],
    final_rows: list[dict[str, Any]],
    per_code_min: int,
    seed: int,
    code_universe: list[str] | None,
) -> dict[str, Any]:
    """Build per-coder sheets from the stratified sample and compute alpha/kappa.

    The stratified sample over final_codes selects the segments (chunk_ids)
    the report is computed on; alpha is computed over exactly the sampled
    segments. Each sheet entry also carries `rated_code_ids`: every code_id
    this coder has an explicit chunk_codes row for on that segment (assign
    OR reject); reliability.py treats a code outside that set as missing
    data rather than a fabricated reject.
    """
    population_rows = [
        {
            "chunk_id": str(r["chunk_id"]),
            "code_id": r["code_id"],
            "disposition": _DISPOSITION.get(r["status"], "no_code_applies"),
        }
        for r in final_rows
    ]
    sampled = stratified_sample(population_rows, per_code_min, seed) if population_rows else []
    # Seed every code present in the population at 0 first, so a code that
    # exists but drew zero rows in the sample still shows up as
    # insufficient instead of being silently absent from `counts`.
    sampled_counts: dict[str, int] = {r["code_id"]: 0 for r in population_rows}
    for r in sampled:
        sampled_counts[r["code_id"]] = sampled_counts.get(r["code_id"], 0) + 1

    # Alpha is computed over exactly the sampled segments, nothing wider.
    sampled_chunks = {r["chunk_id"] for r in sampled}
    entries: dict[str, dict[str, dict[str, Any]]] = {}
    for row in rows:
        segment = str(row["chunk_id"])
        if segment not in sampled_chunks:
            continue
        coder_entries = entries.setdefault(row["coder"], {})
        entry = coder_entries.setdefault(
            segment,
            {"segment_id": segment, "assignments": [], "rated_code_ids": set()},
        )
        entry["rated_code_ids"].add(row["code_id"])
        if row["decision"] == "assign":
            entry["assignments"].append({"code_id": row["code_id"]})
    sheets = [
        {"coder_id": coder, "entries": list(by_segment.values())}
        for coder, by_segment in entries.items()
    ]

    report = computeReliability(sheets, code_universe=code_universe)
    return {
        "status": "success",
        "coders": sorted(entries),
        "n_sheets": len(sheets),
        "sampled_cells": len(sampled),
        "sampled_chunks": len(sampled_chunks),
        "insufficient_codes": insufficient_codes(sampled_counts, per_code_min),
        "insufficient_sample": len(sheets) < 2,
        "overall": report["overall"],
        "per_code": report["per_code"],
        "flags": report["flags"],
    }


async def embed_text(embedder: OllamaEmbedder | PgmlEmbedder, text: str) -> list[float]:
    """Embed one text, keeping blocking calls off the event loop.

    PgmlEmbedder.embed is async; OllamaEmbedder.embed is a synchronous
    round trip, so it runs on a worker thread like coder_client.call_ollama.
    """
    if isinstance(embedder, PgmlEmbedder):
        return await embedder.embed(text)
    return await asyncio.to_thread(embedder.embed, text)


async def embed_codebook(
    pool: CodingStore,
    tenant_id: UUID,
    codebook_doc: dict[str, Any],
    embed: Callable[..., Awaitable[str]],
) -> dict[str, Any]:
    """Validate, embed and load a codebook into code_registry.

    The returned version is the one the loader resolved this codebook's
    content hash to (re-submitting an older codebook returns its own
    version). The loader gets the pool, not an open connection, so the
    embedding calls run with no transaction held. A degraded embedder is
    reported as a retryable error, not a success; re-submitting once the
    embedder is healthy resumes the load.
    """
    try:
        version_id = await load_codebook(codebook_doc, pool, str(tenant_id), embed)
    except CodebookEmbeddingError as exc:
        logger.error("Codebook embedding degraded: %s", exc)
        return {
            "status": "error",
            "error": str(exc),
            "error_type": "embedding_degraded",
            "retryable": True,
            "codes_embedded": 0,
        }
    return {
        "status": "success",
        "codes_embedded": len(codebook_doc.get("categories", [])),
        "codebook_version_id": str(version_id),
    }
