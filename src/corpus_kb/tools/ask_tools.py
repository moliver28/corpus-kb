"""MCP ask tool: the ONE grounded-answer handler every surface routes through.

UX program Wave 1: ``ask`` composes retrieval, generation, sentence-level
citations, and claim verification into one operation. Built like
``tools/search_tools.py`` + ``tools/query_tools.py``: the stack is built
over the CALLER'S pool (per-call embedder + QueryHandler), the
config-driven judge is wired when ``judge.enabled`` (the verify_answer
rule — without it the verifier would silently abstain on every call),
and the LLM is the never-raises ``LlmHandler`` (a down Ollama degrades
to evidence-only with a note, never an error).
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

import asyncpg

from corpus_kb.tools.ingest_common import DEFAULT_TENANT_ID, load_config_or_pass
from corpus_kb.utils.tracing import SPAN_ASK, current_trace_id, span

__all__ = ["ask"]


async def ask(
    query: str,
    config: dict[str, Any] | None = None,
    tenant_id: str = DEFAULT_TENANT_ID,
    k: int | None = None,
    pg_pool: asyncpg.Pool | None = None,
) -> dict[str, Any]:
    """Answer one question from retrieved evidence with cited sentences.

    Delegates to :func:`corpus_kb.ask.compose_answer` — retrieval via
    ``QueryHandler.handle_search_with_meta`` (the same call POST /api/search
    makes, so the honest ``degraded_excluded`` record rides along),
    generation via ``LlmHandler.chat`` at temperature 0, sentence-level
    citation filtering, then optional judge verification. Every surface
    returns the same envelope: ``{"status", "result", "degraded_excluded"}``.
    Honest statuses: ``success``, ``empty`` (no evidence), ``degraded``
    (generation unavailable; evidence only). ``k=None`` (omitted) resolves
    the backend default inside the handler.

    With no ``pg_pool``, a pool is opened from the config's connection
    string and closed after the call (the query_tools convention).

    The whole operation runs inside the ``corpus_kb.ask`` span, so the
    envelope carries ``trace_id`` whenever tracing is enabled (never when
    it is off — the key is absent, not null).
    """
    from corpus_kb.ask.composer import compose_answer
    from corpus_kb.handlers.llm_handler import LlmHandler
    from corpus_kb.handlers.query_handler import QueryHandler
    from corpus_kb.rag import create_embedder

    cfg = load_config_or_pass(config)
    with span(SPAN_ASK):
        own_pool: asyncpg.Pool | None = None
        pool = pg_pool
        if pool is None:
            dsn = str((cfg.get("database", {}) or {}).get("connection_string", ""))
            pool = own_pool = await asyncpg.create_pool(dsn)
        try:
            embedder = create_embedder(cfg, pool)
            handler = QueryHandler(pool, embedder, config=cfg)
            bundle = await compose_answer(
                query,
                handler,
                LlmHandler(cfg),
                judge=_resolve_judge(cfg),
                tenant_id=UUID(tenant_id),
                k=k,
            )
            payload = bundle.to_payload()
            trace_id = current_trace_id()
            if trace_id is not None:
                payload["trace_id"] = trace_id
            return payload
        finally:
            if own_pool is not None:
                await own_pool.close()


def _resolve_judge(cfg: dict[str, Any]) -> object | None:
    """The config-driven judge, wired when judge.enabled (verify_answer rule)."""
    if not (cfg.get("judge", {}) or {}).get("enabled", False):
        return None
    from corpus_kb.rag.judge import OllamaJudge

    return OllamaJudge(cfg)
