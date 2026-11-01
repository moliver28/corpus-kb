"""Coding handler — deductive coding pipeline behind the /api/coding/* routes.

Wraps the real corpus_kb.coding modules:
  - handle_embed_codebook: codebook_loader.load_codebook (validate, embed, write)
  - handle_pool: pooling keyword hits + similarity + coverage reconciliation
  - handle_code_batch: coder_dispatch.dispatch_chunk per requested cell
  - handle_reliability: reliability_sampling + vendored Krippendorff alpha

The operation bodies for pool/code_batch and the reliability report
computation live in :mod:`coding_ops` (a pure move for the 250-line rule);
this module keeps the embed path, the monkeypatch-pinned call sites
(``load_instruct``, ``calibrate_code_floors``, ``compute_saturation``,
``tenant_scope`` — tests patch them HERE), the thin delegating methods,
and the singleton.

Ported from the recovered bundle handler (blob d7293052). Two adaptations:
imports point at corpus_kb.*, and `instruct` is resolved defensively
(load_instruct) until the Embedder Protocol lands in rag/embedder.py.
"""

from __future__ import annotations

import importlib
import logging
from collections.abc import Awaitable, Callable
from typing import Any
from uuid import UUID

from corpus_kb.coding._scope import CodingStore, tenant_scope
from corpus_kb.coding.confidence_routing import Bands
from corpus_kb.coding.floors_calibration import calibrate_code_floors
from corpus_kb.coding.saturation_query import compute_saturation
from corpus_kb.handlers import coding_ops
from corpus_kb.handlers._singleton import SingletonSlot
from corpus_kb.rag.embedder import OllamaEmbedder, PgmlEmbedder

logger = logging.getLogger(__name__)


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
        pool: CodingStore,
        embedder: OllamaEmbedder | PgmlEmbedder | None = None,
        coder: str = "qwen3:8b",
        model: str = "qwen3:8b",
    ) -> None:
        """Take one store: an asyncpg pool (Postgres) or a SqlGateway.

        The gateway is the portable arm (storage portability program, §8):
        every coding module below takes the same store kind, and the pool
        path stays byte-identical.
        """
        self._pool = pool
        self._embedder = embedder
        self._coder = coder
        self._model = model

    async def _embed_text(self, embedder: OllamaEmbedder | PgmlEmbedder, text: str) -> list[float]:
        """Embed one text off the event loop (body in coding_ops.embed_text)."""
        return await coding_ops.embed_text(embedder, text)

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
        """Validate, embed and load a codebook (body in coding_ops.embed_codebook).

        The returned version is the one the loader resolved this codebook's
        content hash to; a degraded embedder is reported as a retryable
        error, not a success with an unpoolable codebook behind it.
        """
        embed = embed_fn or self._default_embed_fn()
        return await coding_ops.embed_codebook(self._pool, tenant_id, codebook_doc, embed)

    async def handle_pool(self, tenant_id: UUID, codebook_version_id: UUID) -> dict[str, Any]:
        """Run pooling + coverage reconciliation (body in coding_ops)."""
        return await coding_ops.pool_summaries(self._pool, tenant_id, codebook_version_id)

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
        (The calibrate_code_floors call site stays in THIS module on
        purpose — tests patch the name here.)
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
        is supplied. (The compute_saturation call site stays in THIS module
        on purpose — tests patch the name here.)
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
        """Code a batch of cells (body in coding_ops.code_batch)."""
        return await coding_ops.code_batch(
            self._pool,
            tenant_id,
            cells,
            coder or self._coder,
            model or self._model,
            call_fn,
            batch_id,
            coding_pass,
            bands,
        )

    async def handle_reliability(
        self,
        tenant_id: UUID,
        per_code_min: int = 5,
        seed: int = 17,
        code_universe: list[str] | None = None,
    ) -> dict[str, Any]:
        """Build per-coder sheets from the stratified sample and compute alpha/kappa.

        The tenant-scoped fetch stays in THIS module on purpose (tests patch
        ``tenant_scope`` here); the sampling/sheet/alpha computation is
        :func:`coding_ops.build_reliability_report`. With fewer than two
        coders in the sample the report carries `insufficient_sample` rather
        than fabricated coders. Only the LATEST `pass` per
        (chunk_id, code_id, coder) is read from chunk_codes (DISTINCT ON).
        """
        async with tenant_scope(self._pool, tenant_id) as conn:
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
        return await coding_ops.build_reliability_report(
            [dict(row) for row in rows],
            [dict(row) for row in final_rows],
            per_code_min,
            seed,
            code_universe,
        )


# ============================================================================
# Singleton
# ============================================================================

_coding_handler = SingletonSlot[CodingHandler](
    "CodingHandler not initialized. Call set_coding_handler() during startup."
)


def get_coding_handler() -> CodingHandler:
    """Get the singleton CodingHandler."""
    return _coding_handler.get()


def set_coding_handler(handler: CodingHandler) -> None:
    """Set the singleton (for server wiring)."""
    _coding_handler.set(handler)


def reset_coding_handler() -> None:
    """Reset the singleton (for testing)."""
    _coding_handler.reset()
