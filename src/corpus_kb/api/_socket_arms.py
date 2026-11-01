"""Hand-written socket arms (bespoke coercion, hand-built envelopes).

The mechanical pool-and-call arms are data rows in ``socket_ops``; the
operations here stay functions because each one does something the row
table cannot say: domain command construction, envelope assembly,
imported constant defaults, an ``or``-default quirk, or list coercion.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from corpus_kb.api._body import opt_bool, opt_float, opt_int, opt_str


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


async def _labeling_triage(params: dict[str, Any], tenant_id: str) -> dict[str, Any]:
    from corpus_kb.api.research_http import research_pool
    from corpus_kb.tools import labeling_triage_ops, labeling_triage_tools

    return await labeling_triage_tools.labeling_triage(
        await research_pool(),
        tenant_id=tenant_id,
        cv_folds=opt_int(
            params.get("cv_folds"), labeling_triage_ops.DEFAULT_TRIAGE_FOLDS, name="cv_folds"
        ),
        flag_threshold=opt_float(
            params.get("flag_threshold"),
            labeling_triage_ops.DEFAULT_FLAG_THRESHOLD,
            name="flag_threshold",
        ),
        limit=opt_int(params.get("limit"), labeling_triage_ops.DEFAULT_QUEUE_LIMIT, name="limit"),
    )


async def _extraction_tune(params: dict[str, Any], tenant_id: str) -> dict[str, Any]:
    from corpus_kb.api.research_http import research_pool
    from corpus_kb.tools.extraction_tune_tools import extraction_tune

    raw = params.get("min_confidence")
    return await extraction_tune(
        await research_pool(),
        tenant_id=tenant_id,
        method=str(params.get("method") or "exemplar"),
        profile=opt_str(params.get("profile")),
        min_confidence=opt_float(raw, 0.8, name="min_confidence"),
        limit=opt_int(params.get("limit"), 8, name="limit"),
        dry_run=opt_bool(params.get("dry_run"), False),
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
        limit=opt_int(params.get("limit"), 50, name="limit"),
        model=opt_str(params.get("model")),
    )


async def _analysis_select_method(params: dict[str, Any], tenant_id: str) -> dict[str, Any]:
    from corpus_kb.api.research_http import research_pool
    from corpus_kb.tools.method_select_tools import analysis_select_method

    task = params.get("task_description")
    return await analysis_select_method(
        await research_pool(),
        tenant_id=tenant_id,
        task_description="" if task is None else str(task),
        output_family=opt_str(params.get("output_family")),
        inputs_available=params.get("inputs_available"),
        max_complexity_tier=opt_str(params.get("max_complexity_tier")),
    )


async def _search(params: dict[str, Any], tenant_id: str) -> dict[str, Any]:
    from corpus_kb.domain.models import SearchQuery
    from corpus_kb.handlers.query_handler import get_query_handler
    from corpus_kb.handlers.query_integrity import envelope_to_payload

    query = SearchQuery(
        tenant_id=UUID(tenant_id),
        query=params["query"],
        # k=None keeps the handler contract: reranking.return_top_k decides.
        k=opt_int(params.get("k"), None, name="k"),
        source_type=params.get("source_type"),
        self_query=params.get("self_query"),
    )
    envelope = await get_query_handler().handle_search_with_meta(query)
    # Same additive envelope as POST /api/search (parity contract).
    return envelope_to_payload(envelope)


async def _query(params: dict[str, Any], tenant_id: str) -> dict[str, Any]:
    from corpus_kb.domain.models import RoutedQuery
    from corpus_kb.handlers.router_handler import get_router_handler

    routed_query = RoutedQuery(
        tenant_id=UUID(tenant_id),
        query=params["query"],
        k=opt_int(params.get("k"), None, name="k"),
    )
    result = await get_router_handler().handle_routed_query(routed_query)
    return {"status": "success", "result": result.model_dump(mode="json")}


async def _list_documents(params: dict[str, Any], tenant_id: str) -> dict[str, Any]:
    from corpus_kb.domain.models import ListDocumentsQuery
    from corpus_kb.handlers.query_handler import get_query_handler

    query = ListDocumentsQuery(
        tenant_id=UUID(tenant_id),
        limit=opt_int(params.get("limit"), 100, name="limit"),
        offset=opt_int(params.get("offset"), 0, name="offset"),
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
