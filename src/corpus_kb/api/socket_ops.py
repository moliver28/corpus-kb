"""Socket method table: one arm per catalogue operation (full parity).

The full-surface parity wave replaced the ``elif`` chain in
``api/socket.py`` with this table: one arm per operation, every catalogue
socket surface dispatchable. Two arm shapes:

  * Core arms (ingest/search/documents/graph): small explicit functions,
    ported verbatim from the old dispatch chain — same pools, same
    envelope construction, same lazy imports.
  * Route arms: generated from ``research_http.tool_routes()`` — the SAME
    table the HTTP surface registers. One table, two transports, so a new
    research/release route is socket-reachable the moment it exists.

Error mapping is unchanged: a missing method raises
:class:`MethodNotFoundError` (mapped to -32601 by the server); any other
error maps to -32603 by the server's one catch.
"""

from __future__ import annotations

import inspect
from collections.abc import Awaitable, Callable
from typing import Any
from uuid import UUID

Arm = Callable[[dict[str, Any], str], Awaitable[dict[str, Any]]]

DEFAULT_TENANT = "00000000-0000-0000-0000-000000000001"


class MethodNotFoundError(Exception):
    """Raised by dispatch when no arm exists for the requested method."""


# ---------------------------------------------------------------------------
# Route arms: generated from the shared HTTP tool-route table
# ---------------------------------------------------------------------------


def _route_arm(route: Any) -> Arm:
    """Build one socket arm from a ``research_http.ToolRoute`` row."""

    async def arm(params: dict[str, Any], tenant_id: str) -> dict[str, Any]:
        from corpus_kb.api.research_http import research_pool

        kwargs: dict[str, Any] = {key: params[key] for key in route.required}
        for key in route.optional:
            if key in params:
                kwargs[key] = params[key]
        if "tenant_id" in inspect.signature(route.fn).parameters:
            kwargs["tenant_id"] = tenant_id
        if route.pool_kw is not None:
            kwargs[route.pool_kw] = await research_pool()
        result = route.fn(**kwargs)
        if inspect.isawaitable(result):
            result = await result
        return result

    return arm


# The notebook tool functions end in ``_tool``; the socket method (and the
# MCP tool name) does not.
_METHOD_FOR_TOOL: dict[str, str] = {
    "notebook_ask_tool": "notebook_ask",
    "notebook_evidence_tool": "notebook_evidence",
    "notebook_uncoded_tool": "notebook_uncoded",
    "notebook_overlap_tool": "notebook_overlap",
}


def _route_arms() -> dict[str, Arm]:
    from corpus_kb.api.research_http import tool_routes

    arms: dict[str, Arm] = {}
    for route in tool_routes():
        name = _METHOD_FOR_TOOL.get(route.fn.__name__, route.fn.__name__)
        arms[name] = _route_arm(route)
    return arms


# ---------------------------------------------------------------------------
# Core arms: the pre-parity methods, ported with their seams intact
# ---------------------------------------------------------------------------


async def _ingest_file(params: dict[str, Any], tenant_id: str) -> dict[str, Any]:
    from corpus_kb.domain.models import IngestFileCommand
    from corpus_kb.handlers.command_handler import get_command_handler

    cmd = IngestFileCommand(
        tenant_id=UUID(tenant_id),
        file_path=params["file_path"],
        content=params.get("content"),
        source_type=params.get("source_type"),
    )
    return await get_command_handler().handle_ingest_file(cmd)


async def _ingest_text(params: dict[str, Any], tenant_id: str) -> dict[str, Any]:
    from corpus_kb.domain.models import IngestTextCommand
    from corpus_kb.handlers.command_handler import get_command_handler

    cmd = IngestTextCommand(
        tenant_id=UUID(tenant_id),
        text=params["text"],
        source=params.get("source", "raw_text"),
        source_type=params.get("source_type", "text"),
    )
    return await get_command_handler().handle_ingest_text(cmd)


async def _ingest_directory(params: dict[str, Any], tenant_id: str) -> dict[str, Any]:
    from corpus_kb.api.research_http import research_pool
    from corpus_kb.tools.ingest_tools import ingest_directory

    return await ingest_directory(
        str(params["directory_path"]),
        await research_pool(),
        tenant_id=tenant_id,
        source_type=params.get("source_type"),
        recursive=bool(params.get("recursive", True)),
    )


async def _ingest_url(params: dict[str, Any], tenant_id: str) -> dict[str, Any]:
    from corpus_kb.api.research_http import research_pool
    from corpus_kb.tools.ingest_tools import ingest_url

    return await ingest_url(
        str(params["url"]),
        await research_pool(),
        tenant_id=tenant_id,
        crawl=bool(params.get("crawl", False)),
        source_type=params.get("source_type"),
    )


async def _ingest_status(params: dict[str, Any], tenant_id: str) -> dict[str, Any]:
    from corpus_kb.api.research_http import research_pool
    from corpus_kb.tools.ingest_surface_tools import ingest_status

    return await ingest_status(
        await research_pool(),
        tenant_id=tenant_id,
        limit=int(params.get("limit", 20)),
    )


async def _ingest_reembed(params: dict[str, Any], tenant_id: str) -> dict[str, Any]:
    from corpus_kb.api.research_http import research_pool
    from corpus_kb.tools.ingest_surface_tools import ingest_reembed

    return await ingest_reembed(
        await research_pool(),
        tenant_id=tenant_id,
        limit=params.get("limit"),
        doc_id=params.get("doc_id"),
    )


async def _delete_document(params: dict[str, Any], tenant_id: str) -> dict[str, Any]:
    from corpus_kb.api.research_http import research_pool
    from corpus_kb.tools.ingest_tools import delete_document

    return await delete_document(
        str(params["document_id"]),
        await research_pool(),
        tenant_id=tenant_id,
    )


async def _topics_build(params: dict[str, Any], tenant_id: str) -> dict[str, Any]:
    from corpus_kb.api.research_http import research_pool
    from corpus_kb.tools.topics_tools import topics_build as topics_build_tool

    return await topics_build_tool(await research_pool(), tenant_id=tenant_id)


async def _topics_list(params: dict[str, Any], tenant_id: str) -> dict[str, Any]:
    from corpus_kb.api.research_http import research_pool
    from corpus_kb.tools.topics_tools import topics_list as topics_list_tool

    return await topics_list_tool(
        await research_pool(),
        tenant_id=tenant_id,
        limit=int(params.get("limit", 50)),
    )


async def _search(params: dict[str, Any], tenant_id: str) -> dict[str, Any]:
    from corpus_kb.domain.models import SearchQuery
    from corpus_kb.handlers.query_handler import get_query_handler
    from corpus_kb.handlers.query_integrity import envelope_to_payload

    query = SearchQuery(
        tenant_id=UUID(tenant_id),
        query=params["query"],
        k=params.get("k"),
        source_type=params.get("source_type"),
        self_query=params.get("self_query"),
    )
    envelope = await get_query_handler().handle_search_with_meta(query)
    # Same additive envelope as POST /api/search (parity contract).
    return envelope_to_payload(envelope)


async def _ask(params: dict[str, Any], tenant_id: str) -> dict[str, Any]:
    from corpus_kb.api.research_http import research_pool
    from corpus_kb.tools.ask_tools import ask as ask_tool

    return await ask_tool(
        str(params.get("query", "")),
        tenant_id=tenant_id,
        k=params.get("k"),
        pg_pool=await research_pool(),
    )


async def _query(params: dict[str, Any], tenant_id: str) -> dict[str, Any]:
    from corpus_kb.domain.models import RoutedQuery
    from corpus_kb.handlers.router_handler import get_router_handler

    routed_query = RoutedQuery(
        tenant_id=UUID(tenant_id),
        query=params["query"],
        k=params.get("k"),
    )
    result = await get_router_handler().handle_routed_query(routed_query)
    return {"status": "success", "result": result.model_dump(mode="json")}


async def _list_documents(params: dict[str, Any], tenant_id: str) -> dict[str, Any]:
    from corpus_kb.domain.models import ListDocumentsQuery
    from corpus_kb.handlers.query_handler import get_query_handler

    query = ListDocumentsQuery(
        tenant_id=UUID(tenant_id),
        limit=params.get("limit", 100),
        offset=params.get("offset", 0),
    )
    results = await get_query_handler().handle_list_documents(query)
    return {"status": "success", "result": [r.model_dump(mode="json") for r in results]}


async def _add_entity(params: dict[str, Any], tenant_id: str) -> dict[str, Any]:
    from corpus_kb.domain.models import AddEntityCommand
    from corpus_kb.handlers.command_handler import get_command_handler

    cmd = AddEntityCommand(
        tenant_id=UUID(tenant_id),
        name=params["name"],
        entity_type=params.get("entity_type", "concept"),
        metadata=params.get("metadata", {}),
    )
    return get_command_handler().handle_add_entity(cmd)


async def _verify_answer(params: dict[str, Any], tenant_id: str) -> dict[str, Any]:
    from corpus_kb.domain.models import VerifyAnswerQuery
    from corpus_kb.handlers.query_handler import get_query_handler

    query = VerifyAnswerQuery(
        answer=params.get("answer", ""), chunk_ids=params.get("chunk_ids", [])
    )
    result = await get_query_handler().handle_verify_answer(query)
    return {"status": "success", "result": result.model_dump(mode="json")}


async def _add_relation(params: dict[str, Any], tenant_id: str) -> dict[str, Any]:
    from corpus_kb.domain.models import AddRelationCommand
    from corpus_kb.handlers.command_handler import get_command_handler

    cmd = AddRelationCommand(
        tenant_id=UUID(tenant_id),
        source_entity_id=UUID(params["source_entity_id"]),
        target_entity_id=UUID(params["target_entity_id"]),
        relation_type=params.get("relation_type", "related_to"),
        weight=params.get("weight", 1.0),
        metadata=params.get("metadata", {}),
    )
    return get_command_handler().handle_add_relation(cmd)


async def _search_graph(params: dict[str, Any], tenant_id: str) -> dict[str, Any]:
    from corpus_kb.api.research_http import research_pool
    from corpus_kb.tools.graph_read_tools import search_graph as search_graph_tool

    return await search_graph_tool(
        await research_pool(),
        str(params.get("query", "")),
        tenant_id=tenant_id,
        entity_type=params.get("entity_type"),
        limit=int(params.get("limit", 100)),
    )


_CORE_ARMS: dict[str, Arm] = {
    "ingest_file": _ingest_file,
    "ingest_text": _ingest_text,
    "ingest_directory": _ingest_directory,
    "ingest_url": _ingest_url,
    "ingest_status": _ingest_status,
    "ingest_reembed": _ingest_reembed,
    "delete_document": _delete_document,
    "topics_build": _topics_build,
    "topics_list": _topics_list,
    "search": _search,
    "ask": _ask,
    "query": _query,
    "list_documents": _list_documents,
    "add_entity": _add_entity,
    "verify_answer": _verify_answer,
    "add_relation": _add_relation,
    "search_graph": _search_graph,
}


def arms() -> dict[str, Arm]:
    """The full socket method table (core arms + generated route arms)."""
    table: dict[str, Arm] = dict(_CORE_ARMS)
    for name, arm in _route_arms().items():
        table.setdefault(name, arm)
    return table


async def dispatch(method: str, params: dict[str, Any]) -> dict[str, Any]:
    """Run one socket method; raise MethodNotFoundError when unknown."""
    arm = arms().get(method)
    if arm is None:
        raise MethodNotFoundError(method)
    tenant_id = str(params.get("tenant_id", DEFAULT_TENANT))
    return await arm(params, tenant_id)
