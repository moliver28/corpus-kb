"""Analysis method routes (AMR program wave 2).

Twelve thin adapters, one per catalogue row:
POST /api/analysis/<kebab of the op name> — see
``contracts.catalogue_analysis`` for the exact routes. Each route parses
JSON, calls the shared handler, and returns JSONResponse through
``@route_errors`` (api._body) — ONE error path. Handler imports stay
inside the route bodies so tests can monkeypatch the tool-module
functions. An honest ``unavailable`` guard answers 503; every other
outcome (including the ``empty``/``disabled`` no-ops) answers 200.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from starlette.requests import Request
from starlette.responses import JSONResponse

from corpus_kb.api._body import parse_body, route_errors
from corpus_kb.contracts.model import DEFAULT_TENANT_ID as _TENANT_DEFAULT


async def _pool() -> Any:
    from corpus_kb.api.research_http import research_pool

    return await research_pool()


async def _respond(
    handler: Callable[..., Awaitable[dict[str, Any]]],
    kwargs: dict[str, Any],
) -> JSONResponse:
    """One handler call + the shared status mapping (the family rule)."""
    result = await handler(await _pool(), **kwargs)
    status_code = 503 if result.get("status") == "unavailable" else 200
    return JSONResponse(result, status_code=status_code)


@route_errors
async def analysis_select_method_route(request: Request) -> JSONResponse:
    """POST /api/analysis/select-method — rank register methods."""
    from corpus_kb.tools.method_select_tools import analysis_select_method

    body = await parse_body(request)
    task = body.get("task_description")
    return await _respond(
        analysis_select_method,
        {
            "task_description": "" if task is None else str(task),
            "output_family": body.get("output_family"),
            "inputs_available": body.get("inputs_available"),
            "max_complexity_tier": body.get("max_complexity_tier"),
            "tenant_id": body.get("tenant_id", _TENANT_DEFAULT),
        },
    )


@route_errors
async def keyphrases_extract_route(request: Request) -> JSONResponse:
    """POST /api/analysis/keyphrases-extract — rank keyphrase candidates."""
    from corpus_kb.tools.keyphrase_tools import keyphrases_extract

    body = await parse_body(request)
    return await _respond(
        keyphrases_extract,
        {
            "variant": str(body.get("variant", "tfidf")),
            "limit": int(body.get("limit", 20)),
            "chunk_ids": body.get("chunk_ids"),
            "tenant_id": body.get("tenant_id", _TENANT_DEFAULT),
        },
    )


@route_errors
async def lexical_classify_route(request: Request) -> JSONResponse:
    """POST /api/analysis/lexical-classify — TF-IDF + logistic screen."""
    from corpus_kb.tools.lexical_tools import lexical_classify

    body = await parse_body(request)
    return await _respond(
        lexical_classify,
        {
            "chunk_ids": body.get("chunk_ids"),
            "tenant_id": body.get("tenant_id", _TENANT_DEFAULT),
        },
    )


@route_errors
async def neighbors_classify_route(request: Request) -> JSONResponse:
    """POST /api/analysis/neighbors-classify — kNN vote labels."""
    from corpus_kb.tools.neighbor_tools import neighbors_classify

    body = await parse_body(request)
    return await _respond(
        neighbors_classify,
        {
            "k": int(body.get("k", 5)),
            "chunk_ids": body.get("chunk_ids"),
            "tenant_id": body.get("tenant_id", _TENANT_DEFAULT),
        },
    )


@route_errors
async def centroids_classify_route(request: Request) -> JSONResponse:
    """POST /api/analysis/centroids-classify — nearest-centroid labels."""
    from corpus_kb.tools.centroid_tools import centroids_classify

    body = await parse_body(request)
    return await _respond(
        centroids_classify,
        {
            "abstain_margin": float(body.get("abstain_margin", 0.05)),
            "chunk_ids": body.get("chunk_ids"),
            "tenant_id": body.get("tenant_id", _TENANT_DEFAULT),
        },
    )


@route_errors
async def clusters_inspect_route(request: Request) -> JSONResponse:
    """POST /api/analysis/clusters-inspect — cluster structure report."""
    from corpus_kb.tools.cluster_tools import clusters_inspect

    body = await parse_body(request)
    min_size = body.get("min_cluster_size")
    sample_cap = body.get("sample_cap")
    return await _respond(
        clusters_inspect,
        {
            "min_cluster_size": int(min_size) if min_size is not None else None,
            "sample_cap": int(sample_cap) if sample_cap is not None else None,
            "tenant_id": body.get("tenant_id", _TENANT_DEFAULT),
        },
    )


@route_errors
async def duplicates_screen_route(request: Request) -> JSONResponse:
    """POST /api/analysis/duplicates-screen — near-duplicate pairs."""
    from corpus_kb.tools.vector_screen_tools import duplicates_screen

    body = await parse_body(request)
    return await _respond(
        duplicates_screen,
        {
            "threshold": float(body.get("threshold", 0.98)),
            "limit": int(body.get("limit", 100)),
            "tenant_id": body.get("tenant_id", _TENANT_DEFAULT),
        },
    )


@route_errors
async def outliers_rank_route(request: Request) -> JSONResponse:
    """POST /api/analysis/outliers-rank — kNN-distance outlier rank."""
    from corpus_kb.tools.vector_screen_tools import outliers_rank

    body = await parse_body(request)
    return await _respond(
        outliers_rank,
        {
            "k": int(body.get("k", 10)),
            "limit": int(body.get("limit", 20)),
            "tenant_id": body.get("tenant_id", _TENANT_DEFAULT),
        },
    )


@route_errors
async def nli_classify_route(request: Request) -> JSONResponse:
    """POST /api/analysis/nli-classify — zero-shot entailment labels."""
    from corpus_kb.tools.nli_tools import nli_classify

    body = await parse_body(request)
    labels = body.get("labels")
    model = body.get("model")
    return await _respond(
        nli_classify,
        {
            "labels": [str(label) for label in labels] if labels is not None else [],
            "chunk_ids": body.get("chunk_ids"),
            "limit": int(body.get("limit", 50)),
            "model": str(model) if model is not None else None,
            "tenant_id": body.get("tenant_id", _TENANT_DEFAULT),
        },
    )


@route_errors
async def contradictions_screen_route(request: Request) -> JSONResponse:
    """POST /api/analysis/contradictions-screen — NLI contradiction pairs."""
    from corpus_kb.tools.nli_tools import contradictions_screen

    body = await parse_body(request)
    model = body.get("model")
    return await _respond(
        contradictions_screen,
        {
            "candidate_k": int(body.get("candidate_k", 5)),
            "limit": int(body.get("limit", 50)),
            "model": str(model) if model is not None else None,
            "tenant_id": body.get("tenant_id", _TENANT_DEFAULT),
        },
    )


@route_errors
async def sentiment_screen_route(request: Request) -> JSONResponse:
    """POST /api/analysis/sentiment-screen — preset lexicon screen."""
    from corpus_kb.tools.sentiment_tools import sentiment_screen

    body = await parse_body(request)
    limit = body.get("limit")
    return await _respond(
        sentiment_screen,
        {
            "chunk_ids": body.get("chunk_ids"),
            "limit": int(limit) if limit is not None else None,
            "tenant_id": body.get("tenant_id", _TENANT_DEFAULT),
        },
    )


@route_errors
async def active_learn_next_route(request: Request) -> JSONResponse:
    """POST /api/analysis/active-learn-next — next labeling batch."""
    from corpus_kb.tools.active_learn_tools import active_learn_next

    body = await parse_body(request)
    return await _respond(
        active_learn_next,
        {
            "strategy": str(body.get("strategy", "margin")),
            "batch": int(body.get("batch", 10)),
            "tenant_id": body.get("tenant_id", _TENANT_DEFAULT),
        },
    )
