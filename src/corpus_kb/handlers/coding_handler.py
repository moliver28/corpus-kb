"""Coding handler — deductive coding pipeline behind the /api/coding/* routes.

Wraps the real corpus_kb.coding modules:
  - handle_embed_codebook: codebook_loader.load_codebook (validate, embed, write)
  - handle_pool: pooling keyword hits + similarity + coverage reconciliation
  - handle_code_batch: coder_dispatch.dispatch_chunk per requested cell
  - handle_reliability: reliability_sampling + vendored Krippendorff alpha

Ported from the recovered bundle handler (blob d7293052). Two adaptations:
imports point at corpus_kb.*, and `instruct` is resolved defensively
(load_instruct) until the Embedder Protocol lands in rag/embedder.py.
"""

from __future__ import annotations

import asyncio
import importlib
import logging
from collections.abc import Awaitable, Callable
from typing import Any
from uuid import UUID

import asyncpg

from corpus_kb.coding.codebook_loader import CodebookEmbeddingError, load_codebook
from corpus_kb.coding.coder_dispatch import dispatch_chunk
from corpus_kb.coding.confidence_routing import Bands
from corpus_kb.coding.floors_calibration import calibrate_code_floors
from corpus_kb.coding.keyword_governance_report import compute_keyword_governance
from corpus_kb.coding.pooling import (
    materialize_chunk_keyword_hits,
    reconcile_coverage,
    run_similarity_pooling,
)
from corpus_kb.coding.reliability import computeReliability
from corpus_kb.coding.reliability_sampling import insufficient_codes, stratified_sample
from corpus_kb.coding.saturation_query import compute_saturation
from corpus_kb.coding.speaker_role_projection import classify_speaker_roles
from corpus_kb.rag.embedder import OllamaEmbedder, PgmlEmbedder
from corpus_kb.storage.tenant_conn import tenant_connection

logger = logging.getLogger(__name__)

# final_codes.status -> the disposition vocabulary reliability_sampling strata on.
_DISPOSITION = {
    "accepted": "accepted",
    "rejected": "rejected",
    "manual_review": "no_code_applies",
}


def load_instruct() -> Callable[[str], str] | None:
    """Resolve the shared qwen3 instruction-prefix helper, if available.

    `corpus_kb.rag.embedder.instruct` returns the query text prefixed with
    QUERY_INSTRUCTION_PREFIX (Embedder Protocol); until that module lands
    this returns None and instructed embedding degrades to plain embedding
    instead of failing at import time.
    """
    module = importlib.import_module("corpus_kb.rag.embedder")
    fn = getattr(module, "instruct", None)
    if fn is None:
        return None
    instruct_fn: Callable[[str], str] = fn
    return instruct_fn


class CodingHandler:
    """Deductive coding pipeline handler."""

    def __init__(
        self,
        pool: asyncpg.Pool,
        embedder: OllamaEmbedder | PgmlEmbedder | None = None,
        coder: str = "qwen3:8b",
        model: str = "qwen3:8b",
    ) -> None:
        self._pool = pool
        self._embedder = embedder
        self._coder = coder
        self._model = model

    async def _embed_text(self, embedder: OllamaEmbedder | PgmlEmbedder, text: str) -> list[float]:
        """Embed one text, keeping blocking calls off the event loop.

        PgmlEmbedder.embed is async; OllamaEmbedder.embed is a synchronous
        Ollama round trip, so it runs on a worker thread exactly as
        coder_client.call_ollama does.
        """
        if isinstance(embedder, PgmlEmbedder):
            return await embedder.embed(text)
        return await asyncio.to_thread(embedder.embed, text)

    def _default_embed_fn(self) -> Callable[..., Awaitable[str]]:
        """Embed via the shared embedder, reusing the shared instruct prefix.

        The embedder is built once per load rather than once per code, so its
        SHA256 cache spans the whole codebook.
        """
        embedder = self._embedder or OllamaEmbedder()
        instruct = load_instruct()

        async def embed_fn(text: str, instructed: bool = False) -> str:
            payload = instruct(text) if (instructed and instruct) else text
            vector = await self._embed_text(embedder, payload)
            return "[" + ",".join(str(float(v)) for v in vector) + "]"

        return embed_fn

    async def handle_embed_codebook(
        self,
        tenant_id: UUID,
        codebook_doc: dict[str, Any],
        embed_fn: Callable[..., Awaitable[str]] | None = None,
    ) -> dict[str, Any]:
        """Validate, embed and load a codebook into code_registry.

        The returned version is the one the loader resolved this codebook's
        content hash to, not "the newest row for this tenant": re-submitting an
        older codebook returns that codebook's own version.

        The loader is handed the pool, not an open connection, so the embedding
        calls run with no transaction held (see codebook_loader.load_codebook).

        A degraded embedder returns zero vectors instead of raising, so a load
        that hit one is reported as a retryable error rather than a success with
        an unpoolable codebook behind it. Nothing was written; re-submitting the
        same codebook once the embedder is healthy resumes the load.
        """
        embed = embed_fn or self._default_embed_fn()
        try:
            version_id = await load_codebook(codebook_doc, self._pool, str(tenant_id), embed)
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

    async def handle_pool(self, tenant_id: UUID, codebook_version_id: UUID) -> dict[str, Any]:
        """Run keyword-hit materialization, similarity pooling, and coverage reconciliation.

        Also runs keyword governance (collision/over-cap/keyness) over the
        keyword hits this pass just materialized, surfaced under
        `keyword_governance`; speaker-role classification runs after
        reconcile_coverage backfills chunk_status.
        """
        keywords = await materialize_chunk_keyword_hits(self._pool, tenant_id, codebook_version_id)
        similarity = await run_similarity_pooling(self._pool, tenant_id, codebook_version_id)
        coverage = await reconcile_coverage(self._pool, tenant_id)
        governance = await compute_keyword_governance(self._pool, tenant_id, codebook_version_id)
        speaker_roles = await classify_speaker_roles(self._pool, tenant_id)
        return {
            "status": "success",
            "keyword_hits_written": keywords["keyword_hits_written"],
            "chunk_signals_written": similarity["chunk_signals_written"],
            "coverage": coverage,
            "keyword_governance": governance,
            "speaker_roles": speaker_roles,
        }

    async def handle_calibrate_floors(
        self,
        tenant_id: UUID,
        code_id: str,
        codebook_version_id: UUID,
        positive_chunk_ids: list[UUID],
        negative_chunk_ids: list[UUID],
        min_pos: int = 10,
        global_fallback: float | None = None,
    ) -> dict[str, Any]:
        """Calibrate one code's pool_floor/residual_floor from labeled chunk_ids.

        There is no labeled-example data available at codebook-load time, so
        this is its own explicit step, typically fed from accepted/rejected
        chunk_codes decisions once a study has some human-reviewed history.
        """
        try:
            return await calibrate_code_floors(
                self._pool,
                tenant_id,
                code_id,
                codebook_version_id,
                positive_chunk_ids,
                negative_chunk_ids,
                min_pos=min_pos,
                global_fallback=global_fallback,
            )
        except ValueError as exc:
            return {"status": "error", "error": str(exc), "code_id": code_id}

    async def handle_saturation(
        self,
        tenant_id: UUID,
        batch_id: str | None = None,
        threshold: float = 0.05,
        min_samples: int = 50,
    ) -> dict[str, Any]:
        """Report the theoretical-saturation signal (ISR + stop rule) for a tenant.

        `batch_id` scopes the "new codes discovered" side of the stopping rule
        to one coding run; the "should_stop" verdict is only meaningful when it
        is supplied.
        """
        return await compute_saturation(
            self._pool, tenant_id, batch_id=batch_id, threshold=threshold, min_samples=min_samples
        )

    async def handle_code_batch(
        self,
        tenant_id: UUID,
        cells: list[dict[str, Any]],
        coder: str | None = None,
        model: str | None = None,
        call_fn: Callable[[str], Awaitable[dict[str, Any]]] | None = None,
        batch_id: str | None = None,
        coding_pass: int = 1,
        bands: Bands | None = None,
    ) -> dict[str, Any]:
        """Code a batch of cells.

        Request shape: `cells` is a list of {"chunk_id": str|UUID, "code_ids": [str, ...]},
        one entry per dispatch call (<=5 candidate codes each). Response carries the
        per-cell dispatch_chunk results plus batch counters, so a caller can tell
        written cells from validator rejections without re-querying.

        A cell that raises (bad chunk_id, transport failure, DB error) is recorded
        as a per-cell error and counted as rejected; the batch keeps going.
        """
        results: list[dict[str, Any]] = []
        cells_coded = 0
        cells_rejected = 0
        rows_written = 0
        for cell in cells:
            raw_chunk_id = cell.get("chunk_id")
            try:
                chunk_id = (
                    raw_chunk_id if isinstance(raw_chunk_id, UUID) else UUID(str(raw_chunk_id))
                )
                result = await dispatch_chunk(
                    self._pool,
                    tenant_id,
                    chunk_id,
                    list(cell.get("code_ids", [])),
                    coder=coder or self._coder,
                    model=model or self._model,
                    call_fn=call_fn,
                    batch_id=batch_id,
                    coding_pass=coding_pass,
                    bands=bands,
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

    async def handle_reliability(
        self,
        tenant_id: UUID,
        per_code_min: int = 5,
        seed: int = 17,
        code_universe: list[str] | None = None,
    ) -> dict[str, Any]:
        """Build per-coder sheets from the stratified sample and compute alpha/kappa.

        The stratified sample over final_codes selects the segments (chunk_ids)
        the report is computed on; alpha is computed over exactly the sampled
        segments. With fewer than two coders in the sample there is no pairable
        data, so the report is returned with an `insufficient_sample` flag
        rather than fabricated coders.

        Only the LATEST `pass` per (chunk_id, code_id, coder) is read from
        chunk_codes (via DISTINCT ON), so a cell re-coded on a later pass folds
        only its current decision into the coder sheets.

        Each sheet entry also carries `rated_code_ids`: every code_id this coder
        has an explicit chunk_codes row for on that segment (assign OR reject).
        reliability.py uses it to treat a code outside that set as missing data
        rather than a fabricated reject.
        """
        async with tenant_connection(self._pool, tenant_id) as conn:
            rows = await conn.fetch(
                "SELECT DISTINCT ON (chunk_id, code_id, coder) "
                "       chunk_id, code_id, coder, decision "
                "FROM chunk_codes WHERE tenant_id=$1 "
                "ORDER BY chunk_id, code_id, coder, pass DESC",
                tenant_id,
            )
            final_rows = await conn.fetch(
                "SELECT chunk_id, code_id, status FROM final_codes WHERE tenant_id=$1",
                tenant_id,
            )

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


# ============================================================================
# Singleton
# ============================================================================

_coding_handler: CodingHandler | None = None


def get_coding_handler() -> CodingHandler:
    """Get the singleton CodingHandler."""
    if _coding_handler is None:
        raise RuntimeError(
            "CodingHandler not initialized. Call set_coding_handler() during startup."
        )
    return _coding_handler


def set_coding_handler(handler: CodingHandler) -> None:
    """Set the singleton (for server wiring)."""
    global _coding_handler
    _coding_handler = handler


def reset_coding_handler() -> None:
    """Reset the singleton (for testing)."""
    global _coding_handler
    _coding_handler = None
