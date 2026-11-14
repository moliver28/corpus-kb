"""The query handler's construction wiring (the ``query_handler`` split).

Split out of ``query_handler.py`` for the 250-line rule: the settings
assembly (config v2 retrieval knobs, resolved once per construction)
and the singleton accessors. ``get_query_handler`` imports the handler
class lazily, so the module edge stays one-directional.
"""

from __future__ import annotations

from typing import Any

import asyncpg

from corpus_kb.errors import DependencyMissingError
from corpus_kb.handlers.query_multiquery import build_rewriter
from corpus_kb.rag.rerank_settings import load_rerank_settings
from corpus_kb.research.multi_query_settings import load_multi_query_settings
from corpus_kb.research.retrieval_settings import load_retrieval_settings
from corpus_kb.research.search_settings import HnswSettings, load_hnsw_settings
from corpus_kb.storage.gateway import SqlGateway

_query_handler: Any = None


def load_query_settings(config: dict[str, Any]) -> tuple[HnswSettings, Any, HnswSettings, Any, Any]:
    """Config v2 retrieval knobs, resolved once per construction.

    U45/U21: ``rrf_k`` feeds the fusion call; the threshold drives the
    exact-scan arm; the candidate depths drive the live LIMITs; ``mode``
    fails loudly here. U20: HNSW scans. Issue #136: the rewriter builds
    only when multi-query is on.
    """
    rerank = load_rerank_settings(config)
    retrieval = load_retrieval_settings(config)
    hnsw = load_hnsw_settings(config)
    multi = load_multi_query_settings(config)
    return rerank, retrieval, hnsw, multi, build_rewriter(multi)


def get_query_handler(pool: asyncpg.Pool | SqlGateway | None = None) -> Any:
    """Get or create the singleton QueryHandler."""
    global _query_handler
    if _query_handler is None:
        if pool is None:
            # Typed refusal (zero-drift rule 4): the query routes fail with
            # the taxonomy class, never a bare RuntimeError -> 500.
            raise DependencyMissingError(
                "The query handler is not initialized.",
                remediation="Start the server (corpus-kb serve), or pass a store to the caller.",
            )
        from corpus_kb.handlers.query_handler import QueryHandler

        _query_handler = QueryHandler(pool)
    return _query_handler


def set_query_handler(handler: Any) -> None:
    """Set the singleton (for server wiring)."""
    global _query_handler
    _query_handler = handler


def reset_query_handler() -> None:
    """Clear the singleton (the test reset, like ``reset_graph_handler``)."""
    global _query_handler
    _query_handler = None
