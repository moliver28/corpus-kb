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

from corpus_kb.api._body import opt_bool, opt_int, opt_str

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
        validate_mode=opt_str(params.get("validate")),
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
        validate_mode=opt_str(params.get("validate")),
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
        recursive=opt_bool(params.get("recursive"), True),
        validate=opt_str(params.get("validate")),
    )


async def _ingest_url(params: dict[str, Any], tenant_id: str) -> dict[str, Any]:
    from corpus_kb.api.research_http import research_pool
    from corpus_kb.tools.ingest_tools import ingest_url

    return await ingest_url(
        str(params["url"]),
        await research_pool(),
        tenant_id=tenant_id,
        crawl=opt_bool(params.get("crawl"), False),
        source_type=params.get("source_type"),
        validate=opt_str(params.get("validate")),
    )


async def _ingest_status(params: dict[str, Any], tenant_id: str) -> dict[str, Any]:
    from corpus_kb.api.research_http import research_pool
    from corpus_kb.tools.ingest_surface_tools import ingest_status

    project_id = params.get("project_id")
    return await ingest_status(
        await research_pool(),
        tenant_id=tenant_id,
        limit=int(params.get("limit", 20)),
        project_id=str(project_id) if project_id is not None else None,
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


async def _topics_trends(params: dict[str, Any], tenant_id: str) -> dict[str, Any]:
    from corpus_kb.api.research_http import research_pool
    from corpus_kb.tools.topics_tools import topics_trends as topics_trends_tool

    bins = params.get("bins")
    return await topics_trends_tool(
        await research_pool(),
        tenant_id=tenant_id,
        bins=int(bins) if bins is not None else None,
    )


async def _labeling_build(params: dict[str, Any], tenant_id: str) -> dict[str, Any]:
    from corpus_kb.api.research_http import research_pool
    from corpus_kb.tools.labeling_tools import labeling_build as labeling_build_tool

    return await labeling_build_tool(await research_pool(), tenant_id=tenant_id)


async def _labeling_list(params: dict[str, Any], tenant_id: str) -> dict[str, Any]:
    from corpus_kb.api.research_http import research_pool
    from corpus_kb.tools.labeling_tools import labeling_list as labeling_list_tool

    return await labeling_list_tool(
        await research_pool(),
        tenant_id=tenant_id,
        limit=int(params.get("limit", 50)),
    )


async def _labeling_lfs(params: dict[str, Any], tenant_id: str) -> dict[str, Any]:
    from corpus_kb.api.research_http import research_pool
    from corpus_kb.tools.labeling_tools import labeling_lfs as labeling_lfs_tool

    return await labeling_lfs_tool(
        await research_pool(),
        tenant_id=tenant_id,
        limit=int(params.get("limit", 50)),
    )


async def _classification_build(params: dict[str, Any], tenant_id: str) -> dict[str, Any]:
    from corpus_kb.api.research_http import research_pool
    from corpus_kb.tools.classification_tools import (
        classification_build as classification_build_tool,
    )

    seed_file = params.get("seed_file")
    return await classification_build_tool(
        await research_pool(),
        tenant_id=tenant_id,
        seed_file=str(seed_file) if seed_file is not None else None,
    )


async def _classification_list(params: dict[str, Any], tenant_id: str) -> dict[str, Any]:
    from corpus_kb.api.research_http import research_pool
    from corpus_kb.tools.classification_tools import (
        classification_list as classification_list_tool,
    )

    return await classification_list_tool(
        await research_pool(),
        tenant_id=tenant_id,
        limit=int(params.get("limit", 50)),
    )


async def _analysis_backfill_dates(params: dict[str, Any], tenant_id: str) -> dict[str, Any]:
    from corpus_kb.api.research_http import research_pool
    from corpus_kb.tools.analysis_tools import (
        analysis_backfill_dates as analysis_backfill_dates_tool,
    )

    return await analysis_backfill_dates_tool(await research_pool(), tenant_id=tenant_id)


async def _analyze_run(params: dict[str, Any], tenant_id: str) -> dict[str, Any]:
    from corpus_kb.api.research_http import research_pool
    from corpus_kb.tools.analysis_tools import analyze_run as analyze_run_tool

    output = params.get("output")
    return await analyze_run_tool(
        await research_pool(),
        tenant_id=tenant_id,
        output=str(output) if output is not None else None,
    )


# -- AMR program wave 2: the method ops (one arm per catalogue row; same
#    lazy-import + pool shape as the analysis arms above) ------------------


def _opt_int(params: dict[str, Any], key: str) -> int | None:
    raw = params.get(key)
    return int(raw) if raw is not None else None


def _opt_str(params: dict[str, Any], key: str) -> str | None:
    raw = params.get(key)
    return str(raw) if raw is not None else None


async def _statements_list(params: dict[str, Any], tenant_id: str) -> dict[str, Any]:
    from corpus_kb.api.research_http import research_pool
    from corpus_kb.tools.statements_tools import statements_list

    return await statements_list(
        await research_pool(),
        tenant_id=tenant_id,
        template_id=_opt_str(params, "template_id"),
        document_id=_opt_str(params, "document_id"),
        limit=opt_int(params.get("limit"), 50),
    )


async def _statements_timeline(params: dict[str, Any], tenant_id: str) -> dict[str, Any]:
    from corpus_kb.api.research_http import research_pool
    from corpus_kb.tools.statements_tools import statements_timeline

    return await statements_timeline(
        await research_pool(),
        tenant_id=tenant_id,
        text_query=_opt_str(params, "text_query"),
        limit=opt_int(params.get("limit"), 200),
    )


async def _conflicts_scan(params: dict[str, Any], tenant_id: str) -> dict[str, Any]:
    from corpus_kb.api.research_http import research_pool
    from corpus_kb.tools.conflicts_tools import conflicts_scan

    return await conflicts_scan(await research_pool(), tenant_id=tenant_id)


async def _extraction_tune(params: dict[str, Any], tenant_id: str) -> dict[str, Any]:
    from corpus_kb.api.research_http import research_pool
    from corpus_kb.tools.extraction_tune_tools import extraction_tune

    raw = params.get("min_confidence")
    try:
        min_confidence = float(raw) if raw is not None else 0.8
    except (TypeError, ValueError):
        min_confidence = 0.8
    return await extraction_tune(
        await research_pool(),
        tenant_id=tenant_id,
        method=str(params.get("method") or "exemplar"),
        profile=_opt_str(params, "profile"),
        min_confidence=min_confidence,
        limit=opt_int(params.get("limit"), 8),
        dry_run=opt_bool(params.get("dry_run"), False),
    )


async def _conflicts_list(params: dict[str, Any], tenant_id: str) -> dict[str, Any]:
    from corpus_kb.api.research_http import research_pool
    from corpus_kb.tools.conflicts_tools import conflicts_list

    return await conflicts_list(
        await research_pool(),
        tenant_id=tenant_id,
        verdict=_opt_str(params, "verdict"),
        limit=opt_int(params.get("limit"), 50),
    )


async def _conflicts_resolve(params: dict[str, Any], tenant_id: str) -> dict[str, Any]:
    from corpus_kb.api.research_http import research_pool
    from corpus_kb.tools.conflicts_tools import conflicts_resolve

    return await conflicts_resolve(
        await research_pool(),
        tenant_id=tenant_id,
        conflict_id=str(params.get("conflict_id", "")),
        resolved_verdict=str(params.get("resolved_verdict", "")),
        resolved_by=str(params.get("resolved_by", "")),
    )


async def _queue_enqueue(params: dict[str, Any], tenant_id: str) -> dict[str, Any]:
    from corpus_kb.api.research_http import research_pool
    from corpus_kb.tools.queue_tools import queue_enqueue

    return await queue_enqueue(
        await research_pool(),
        tenant_id=tenant_id,
        path=str(params.get("path", "")),
        title=_opt_str(params, "title"),
        profile=_opt_str(params, "profile"),
        strategy_config=params.get("strategy_config"),
        project_id=_opt_str(params, "project_id"),
    )


async def _queue_status(params: dict[str, Any], tenant_id: str) -> dict[str, Any]:
    from corpus_kb.api.research_http import research_pool
    from corpus_kb.tools.queue_tools import queue_status

    return await queue_status(
        await research_pool(),
        tenant_id=tenant_id,
        queue_id=_opt_str(params, "queue_id"),
        limit=opt_int(params.get("limit"), 50),
    )


async def _queue_cancel(params: dict[str, Any], tenant_id: str) -> dict[str, Any]:
    from corpus_kb.api.research_http import research_pool
    from corpus_kb.tools.queue_tools import queue_cancel

    return await queue_cancel(
        await research_pool(),
        tenant_id=tenant_id,
        queue_id=str(params.get("queue_id", "")),
    )


async def _beliefs_current(params: dict[str, Any], tenant_id: str) -> dict[str, Any]:
    from corpus_kb.api.research_http import research_pool
    from corpus_kb.tools.beliefs_tools import beliefs_current

    return await beliefs_current(
        await research_pool(),
        tenant_id=tenant_id,
        subject=_opt_str(params, "subject"),
        limit=opt_int(params.get("limit"), 50),
        rebuild=opt_bool(params.get("rebuild"), True),
    )


async def _beliefs_triple(params: dict[str, Any], tenant_id: str, op_name: str) -> dict[str, Any]:
    from corpus_kb.api.research_http import research_pool
    from corpus_kb.tools import beliefs_tools

    handler = getattr(beliefs_tools, op_name)
    return await handler(
        await research_pool(),
        tenant_id=tenant_id,
        subject=_opt_str(params, "subject"),
        relation=_opt_str(params, "relation"),
        object=_opt_str(params, "object"),
    )


async def _beliefs_timeline(params: dict[str, Any], tenant_id: str) -> dict[str, Any]:
    return await _beliefs_triple(params, tenant_id, "beliefs_timeline")


async def _beliefs_predict(params: dict[str, Any], tenant_id: str) -> dict[str, Any]:
    return await _beliefs_triple(params, tenant_id, "beliefs_predict")


async def _beliefs_compare(params: dict[str, Any], tenant_id: str) -> dict[str, Any]:
    from corpus_kb.api.research_http import research_pool
    from corpus_kb.tools.beliefs_tools import beliefs_compare

    return await beliefs_compare(
        await research_pool(),
        tenant_id=tenant_id,
        subject=_opt_str(params, "subject"),
    )


async def _analysis_select_method(params: dict[str, Any], tenant_id: str) -> dict[str, Any]:
    from corpus_kb.api.research_http import research_pool
    from corpus_kb.tools.method_select_tools import analysis_select_method

    task = params.get("task_description")
    return await analysis_select_method(
        await research_pool(),
        tenant_id=tenant_id,
        task_description="" if task is None else str(task),
        output_family=_opt_str(params, "output_family"),
        inputs_available=params.get("inputs_available"),
        max_complexity_tier=_opt_str(params, "max_complexity_tier"),
    )


async def _keyphrases_extract(params: dict[str, Any], tenant_id: str) -> dict[str, Any]:
    from corpus_kb.api.research_http import research_pool
    from corpus_kb.tools.keyphrase_tools import keyphrases_extract

    return await keyphrases_extract(
        await research_pool(),
        tenant_id=tenant_id,
        variant=str(params.get("variant", "tfidf")),
        limit=int(params.get("limit", 20)),
        chunk_ids=params.get("chunk_ids"),
    )


async def _lexical_classify(params: dict[str, Any], tenant_id: str) -> dict[str, Any]:
    from corpus_kb.api.research_http import research_pool
    from corpus_kb.tools.lexical_tools import lexical_classify

    return await lexical_classify(
        await research_pool(),
        tenant_id=tenant_id,
        chunk_ids=params.get("chunk_ids"),
    )


async def _neighbors_classify(params: dict[str, Any], tenant_id: str) -> dict[str, Any]:
    from corpus_kb.api.research_http import research_pool
    from corpus_kb.tools.neighbor_tools import neighbors_classify

    return await neighbors_classify(
        await research_pool(),
        tenant_id=tenant_id,
        k=int(params.get("k", 5)),
        chunk_ids=params.get("chunk_ids"),
    )


async def _centroids_classify(params: dict[str, Any], tenant_id: str) -> dict[str, Any]:
    from corpus_kb.api.research_http import research_pool
    from corpus_kb.tools.centroid_tools import centroids_classify

    return await centroids_classify(
        await research_pool(),
        tenant_id=tenant_id,
        abstain_margin=float(params.get("abstain_margin", 0.05)),
        chunk_ids=params.get("chunk_ids"),
    )


async def _clusters_inspect(params: dict[str, Any], tenant_id: str) -> dict[str, Any]:
    from corpus_kb.api.research_http import research_pool
    from corpus_kb.tools.cluster_tools import clusters_inspect

    return await clusters_inspect(
        await research_pool(),
        tenant_id=tenant_id,
        min_cluster_size=_opt_int(params, "min_cluster_size"),
        sample_cap=_opt_int(params, "sample_cap"),
    )


async def _duplicates_screen(params: dict[str, Any], tenant_id: str) -> dict[str, Any]:
    from corpus_kb.api.research_http import research_pool
    from corpus_kb.tools.vector_screen_tools import duplicates_screen

    return await duplicates_screen(
        await research_pool(),
        tenant_id=tenant_id,
        threshold=float(params.get("threshold", 0.98)),
        limit=int(params.get("limit", 100)),
    )


async def _outliers_rank(params: dict[str, Any], tenant_id: str) -> dict[str, Any]:
    from corpus_kb.api.research_http import research_pool
    from corpus_kb.tools.vector_screen_tools import outliers_rank

    return await outliers_rank(
        await research_pool(),
        tenant_id=tenant_id,
        k=int(params.get("k", 10)),
        limit=int(params.get("limit", 20)),
    )


async def _nli_classify(params: dict[str, Any], tenant_id: str) -> dict[str, Any]:
    from corpus_kb.api.research_http import research_pool
    from corpus_kb.tools.nli_tools import nli_classify

    labels = params.get("labels")
    return await nli_classify(
        await research_pool(),
        tenant_id=tenant_id,
        labels=[str(label) for label in labels] if labels is not None else [],
        chunk_ids=params.get("chunk_ids"),
        limit=int(params.get("limit", 50)),
        model=_opt_str(params, "model"),
    )


async def _contradictions_screen(params: dict[str, Any], tenant_id: str) -> dict[str, Any]:
    from corpus_kb.api.research_http import research_pool
    from corpus_kb.tools.nli_tools import contradictions_screen

    return await contradictions_screen(
        await research_pool(),
        tenant_id=tenant_id,
        candidate_k=int(params.get("candidate_k", 5)),
        limit=int(params.get("limit", 50)),
        model=_opt_str(params, "model"),
    )


async def _sentiment_screen(params: dict[str, Any], tenant_id: str) -> dict[str, Any]:
    from corpus_kb.api.research_http import research_pool
    from corpus_kb.tools.sentiment_tools import sentiment_screen

    return await sentiment_screen(
        await research_pool(),
        tenant_id=tenant_id,
        chunk_ids=params.get("chunk_ids"),
        limit=_opt_int(params, "limit"),
    )


async def _active_learn_next(params: dict[str, Any], tenant_id: str) -> dict[str, Any]:
    from corpus_kb.api.research_http import research_pool
    from corpus_kb.tools.active_learn_tools import active_learn_next

    return await active_learn_next(
        await research_pool(),
        tenant_id=tenant_id,
        strategy=str(params.get("strategy", "margin")),
        batch=int(params.get("batch", 10)),
    )


async def _analysis_embed_meta(params: dict[str, Any], tenant_id: str) -> dict[str, Any]:
    from corpus_kb.api.research_http import research_pool
    from corpus_kb.tools.meta_embed_tools import analysis_embed_meta

    raw_kinds = params.get("kinds")
    return await analysis_embed_meta(
        await research_pool(),
        tenant_id=tenant_id,
        kinds=str(raw_kinds) if raw_kinds is not None else None,
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


async def _get_entity_relations(params: dict[str, Any], tenant_id: str) -> dict[str, Any]:
    from corpus_kb.api.research_http import research_pool
    from corpus_kb.tools.graph_read_tools import get_entity_relations as relations_tool

    raw_limit = params.get("limit")
    return await relations_tool(
        await research_pool(),
        str(params["entity_id"]),
        tenant_id=tenant_id,
        limit=int(raw_limit) if raw_limit is not None else None,
    )


async def _graph_neighbors(params: dict[str, Any], tenant_id: str) -> dict[str, Any]:
    from corpus_kb.api.research_http import research_pool
    from corpus_kb.tools.graph_read_tools import graph_neighbors as neighbors_tool

    return await neighbors_tool(
        await research_pool(),
        str(params["entity_id"]),
        tenant_id=tenant_id,
        hops=int(params.get("hops", 1)),
        direction=str(params.get("direction", "both")),
        relation_types=params.get("relation_types"),
        entity_types=params.get("entity_types"),
        limit=int(params.get("limit", 100)),
    )


async def _graph_traverse(params: dict[str, Any], tenant_id: str) -> dict[str, Any]:
    from corpus_kb.api.research_http import research_pool
    from corpus_kb.tools.graph_read_tools import graph_traverse as traverse_tool

    raw_depth = params.get("max_depth")
    return await traverse_tool(
        await research_pool(),
        str(params["entity_id"]),
        tenant_id=tenant_id,
        max_depth=int(raw_depth) if raw_depth is not None else None,
        relation_types=params.get("relation_types"),
        entity_types=params.get("entity_types"),
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
    # Analysis program Wave 0 (labeling/classification/analysis families
    # + the topics trends op): honest no-op stubs until the engines land.
    "topics_trends": _topics_trends,
    "labeling_build": _labeling_build,
    "labeling_list": _labeling_list,
    "labeling_lfs": _labeling_lfs,
    "classification_build": _classification_build,
    "classification_list": _classification_list,
    "analysis_backfill_dates": _analysis_backfill_dates,
    "analyze_run": _analyze_run,
    # AMR program wave 2: the method ops.
    "analysis_select_method": _analysis_select_method,
    "keyphrases_extract": _keyphrases_extract,
    "lexical_classify": _lexical_classify,
    "neighbors_classify": _neighbors_classify,
    "centroids_classify": _centroids_classify,
    "clusters_inspect": _clusters_inspect,
    "duplicates_screen": _duplicates_screen,
    "outliers_rank": _outliers_rank,
    "nli_classify": _nli_classify,
    "contradictions_screen": _contradictions_screen,
    "sentiment_screen": _sentiment_screen,
    "active_learn_next": _active_learn_next,
    # Living Corpus W6: the meta-embedding build op.
    "analysis_embed_meta": _analysis_embed_meta,
    # Living Corpus W2: the statement-atom read ops.
    "statements_list": _statements_list,
    "statements_timeline": _statements_timeline,
    # Living Corpus W3: the conflict ops.
    "conflicts_scan": _conflicts_scan,
    "conflicts_list": _conflicts_list,
    "conflicts_resolve": _conflicts_resolve,
    # Living Corpus W4: the belief ops.
    "beliefs_current": _beliefs_current,
    "beliefs_timeline": _beliefs_timeline,
    "beliefs_predict": _beliefs_predict,
    "beliefs_compare": _beliefs_compare,
    # Textbook-ingestion program (Wave 1): the ingest queue ops.
    "queue_enqueue": _queue_enqueue,
    "queue_status": _queue_status,
    "queue_cancel": _queue_cancel,
    # Living Corpus W5: the tune op.
    "extraction_tune": _extraction_tune,
    "search": _search,
    "ask": _ask,
    "query": _query,
    "list_documents": _list_documents,
    "add_entity": _add_entity,
    "verify_answer": _verify_answer,
    "add_relation": _add_relation,
    "search_graph": _search_graph,
    "get_entity_relations": _get_entity_relations,
    "graph_neighbors": _graph_neighbors,
    "graph_traverse": _graph_traverse,
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
