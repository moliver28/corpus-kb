"""Integration tests for self-query structured filtering against handle_search."""

from __future__ import annotations

import pytest

from corpus_kb.domain.models import DEFAULT_TENANT_ID, SearchQuery
from corpus_kb.handlers.query_handler import QueryHandler
from corpus_kb.rag.embedder import FakeEmbedder
from corpus_kb.rag.self_query import ParsedQuery, Predicate
from corpus_kb.storage.tenant_conn import tenant_connection
from corpus_kb.tools.ingest_common import ingest_text


@pytest.mark.asyncio
async def test_self_query_predicate_narrows_results(pg_pool) -> None:
    """A self-query source_type predicate must genuinely narrow results, not
    pass vacuously because no row matched. "pdf" is not a real source_type
    this codebase ever produces -- ingest_text validates against
    {"code", "markdown", "text"} (the internal chunking-strategy dispatch),
    and ingest_file's own extension-based detection (_detect_source_type in
    ingest_tools.py) only ever returns those same three values. Use
    "markdown" vs "text", the actual reachable values, so the predicate has
    real matching data to prove the filter against.

    corpus_kb_test is a persistent, shared database (see other tests'
    "_delete_document" helpers) and legitimately has OTHER source_type
    "markdown" documents left over from unrelated tests -- so this test
    cannot assert every result is exclusively its own "markdown" document.
    It asserts the two things the predicate actually guarantees: the
    "text"-type sibling document is excluded, and this test's own
    "markdown" document is present.
    """
    config = {
        "graph": {"extract_entities": False},
        "embedding": {"model": "qwen3-embedding:8b", "dimensions": 4096},
    }
    async with tenant_connection(pg_pool, DEFAULT_TENANT_ID) as conn:
        await conn.execute(
            "DELETE FROM documents WHERE tenant_id = $1 AND source = ANY($2)",
            str(DEFAULT_TENANT_ID),
            ["test-filter-markdown", "test-filter-text"],
        )
    unique_phrase = "zzyzx quixotic narwhal budget spend"
    md_result = await ingest_text(
        text=f"{unique_phrase} markdown edition",
        pg_pool=pg_pool,
        source_type="markdown",
        config=config,
        source="test-filter-markdown",
    )
    txt_result = await ingest_text(
        text=f"{unique_phrase} text edition",
        pg_pool=pg_pool,
        source_type="text",
        config=config,
        source="test-filter-text",
    )
    assert md_result["status"] == "success"
    assert txt_result["status"] == "success"

    class _StubParser:
        def parse(self, query: str) -> ParsedQuery:
            return ParsedQuery(
                semantic_query=query,
                predicates=[Predicate(target="documents.source_type", op="=", value="markdown")],
            )

    handler = QueryHandler(
        pg_pool,
        embedder=FakeEmbedder(config),
        self_query_parser=_StubParser(),
        config={"search": {"self_query": {"enabled": True}}},
    )
    results = await handler.handle_search(SearchQuery(query=unique_phrase, k=20, self_query=True))
    result_sources = {r.source for r in results}
    assert len(results) > 0, "predicate matched zero rows -- test proves nothing"
    assert "test-filter-markdown" in result_sources
    assert "test-filter-text" not in result_sources
