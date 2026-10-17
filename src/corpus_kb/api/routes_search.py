"""Search/query route family (zero-drift split of api/http.py).

POST /api/search, /api/search/similar, /api/search/context, /api/verify
POST /api/query, /api/query/sql, /api/embed

Thin adapters: parse JSON, call the shared handler layer, return
JSONResponse. Every route runs through ``@route_errors`` (api._body) —
ONE error path. Handler imports stay inside the route bodies so tests can
monkeypatch the handler-module singletons.
"""

from __future__ import annotations

from uuid import UUID

from starlette.requests import Request
from starlette.responses import JSONResponse

from corpus_kb.api._body import parse_body, route_errors
from corpus_kb.domain.models import (
    RoutedQuery,
    SearchContextQuery,
    SearchQuery,
    SearchSimilarQuery,
    SQLQuery,
    VerifyAnswerQuery,
)


@route_errors
async def search(request: Request) -> JSONResponse:
    """POST /api/search — hybrid vector + FTS search."""
    from corpus_kb.handlers.query_handler import get_query_handler
    from corpus_kb.handlers.query_integrity import envelope_to_payload

    body = await parse_body(request)
    query = SearchQuery(
        tenant_id=UUID(body.get("tenant_id", "00000000-0000-0000-0000-000000000001")),
        query=body["query"],
        k=body.get("k"),
        source_type=body.get("source_type"),
        self_query=body.get("self_query"),
    )
    handler = get_query_handler()
    envelope = await handler.handle_search_with_meta(query)
    # Additive honest envelope: legacy {status, result} consumers keep
    # working; degraded_excluded (+remedy on degraded_empty) is new.
    return JSONResponse(envelope_to_payload(envelope))


@route_errors
async def search_similar(request: Request) -> JSONResponse:
    """POST /api/search/similar — find chunks similar to a given chunk."""
    from corpus_kb.handlers.query_handler import get_query_handler

    body = await parse_body(request)
    query = SearchSimilarQuery(
        tenant_id=UUID(body.get("tenant_id", "00000000-0000-0000-0000-000000000001")),
        chunk_id=UUID(body["chunk_id"]),
        k=body.get("k", 10),
    )
    handler = get_query_handler()
    results = await handler.handle_search_similar(query)
    return JSONResponse(
        {"status": "success", "result": [r.model_dump(mode="json") for r in results]}
    )


@route_errors
async def search_context(request: Request) -> JSONResponse:
    """POST /api/search/context — search with surrounding context chunks."""
    from corpus_kb.handlers.query_handler import get_query_handler

    body = await parse_body(request)
    query = SearchContextQuery(
        tenant_id=UUID(body.get("tenant_id", "00000000-0000-0000-0000-000000000001")),
        query=body["query"],
        k=body.get("k", 5),
        context_chunks=body.get("context_chunks", 2),
    )
    handler = get_query_handler()
    results = await handler.handle_search_context(query)
    return JSONResponse(
        {"status": "success", "result": [r.model_dump(mode="json") for r in results]}
    )


@route_errors
async def verify(request: Request) -> JSONResponse:
    """POST /api/verify — claim-level groundedness verification against cited chunks."""
    from corpus_kb.handlers.query_handler import get_query_handler

    body = await parse_body(request)
    query = VerifyAnswerQuery(answer=body.get("answer", ""), chunk_ids=body.get("chunk_ids", []))
    result = await get_query_handler().handle_verify_answer(query)
    return JSONResponse({"status": "success", "result": result.model_dump(mode="json")})


@route_errors
async def route_query(request: Request) -> JSONResponse:
    """POST /api/query — adaptive routing across vector/SQL/graph backends."""
    from corpus_kb.handlers.router_handler import get_router_handler

    body = await parse_body(request)
    query = RoutedQuery(
        tenant_id=UUID(body.get("tenant_id", "00000000-0000-0000-0000-000000000001")),
        query=body["query"],
        k=body.get("k"),
    )
    handler = get_router_handler()
    result = await handler.handle_routed_query(query)
    return JSONResponse({"status": "success", "result": result.model_dump(mode="json")})


@route_errors
async def query_sql(request: Request) -> JSONResponse:
    """POST /api/query/sql — execute a read-only SQL query."""
    from corpus_kb.handlers.query_handler import get_query_handler

    body = await parse_body(request)
    query = SQLQuery(
        tenant_id=UUID(body.get("tenant_id", "00000000-0000-0000-0000-000000000001")),
        sql=body["sql"],
        params=body.get("params", {}),
    )
    handler = get_query_handler()
    results = await handler.handle_sql_query(query)
    return JSONResponse({"status": "success", "result": results})


@route_errors
async def embed_text(request: Request) -> JSONResponse:
    """POST /api/embed — embed text with optional instruction prefix."""
    import asyncio

    from corpus_kb.handlers.coding_handler import load_instruct
    from corpus_kb.rag.embedder import OllamaEmbedder

    body = await parse_body(request)
    text = str(body.get("text", ""))
    requested_instructed = bool(body.get("instructed", False))

    if not text:
        return JSONResponse({"error": "text is required"}, status_code=400)

    instruct = load_instruct()
    payload = instruct(text) if (instruct is not None and requested_instructed) else text
    # Per-request embedder: single texts gain nothing from a shared cache,
    # and the blocking Ollama round trip runs off the event loop.
    vector = await asyncio.to_thread(OllamaEmbedder().embed, payload)
    return JSONResponse(
        {
            "vector": vector,
            "instructed": instruct is not None and requested_instructed,
            "dimensions": len(vector),
        }
    )
