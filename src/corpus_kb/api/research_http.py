"""Research + release HTTP routes (U5, P7) — Starlette thin adapter.

The release lifecycle (v6 §6) and every mechanically HTTP-shaped research
operation get routes HERE; each route parses its body, calls the SAME
shared layer the CLI uses (``tools/release_tools.py`` /
``tools/research_tools.py`` / ``tools/notebook_tools.py`` — one handler per
operation), and serializes the payload. Zero lifecycle logic in this file.

Surface mapping lives in ``surface_registry.SURFACES`` (``http_route``);
``tests/test_surface_parity.py`` pins every registry route onto the real
route table from ``create_http_app()``. The two operations WITHOUT routes
(``research-cycle`` interactive postures, ``research-demo`` CLI-only) are
the documented parity allowlist in ``surface_registry_parity.py``.

Status mapping (honest codes, zero-drift rule 4): a ``blocked`` release
refusal -> 409; an ``unavailable`` payload (pre-migration database, missing
extra) -> 503; a reported ``error`` payload -> the status its ``error_type``
maps to (400 when it names none); raised exceptions -> the one taxonomy
path (``api._body.error_response``); everything the handler REPORTS as a
result (``not_evaluable`` gates included) -> 200.
"""

from __future__ import annotations

import inspect
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, get_type_hints

from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from corpus_kb.api._body import DEFAULT_TENANT_ID, error_response, opt_int, parse_body
from corpus_kb.errors import error_status

# ---------------------------------------------------------------------------
# Pool access (lazy singleton; server wiring / tests inject via set_research_pool)
# ---------------------------------------------------------------------------

_pool: Any = None


def set_research_pool(pool: Any) -> None:
    """Inject a pool (server wiring, tests)."""
    global _pool
    _pool = pool


async def research_pool() -> Any:
    """The shared asyncpg pool for research routes (created on first use)."""
    global _pool
    if _pool is None:
        import asyncpg

        from corpus_kb.config import load_config
        from corpus_kb.errors import DependencyMissingError

        cfg = load_config()
        db = cfg.get("database") or {}
        if str((db or {}).get("backend") or "postgres") == "duckdb":
            # Typed refusal (zero-drift rule 4): creating an asyncpg pool on
            # a DuckDB file path fails raw; name the real requirement first.
            raise DependencyMissingError(
                "The research routes need the Postgres pool.",
                remediation=(
                    "Run the server on the Postgres backend."
                    " The DuckDB port for these routes is pending."
                ),
            )
        _pool = await asyncpg.create_pool(str(db.get("connection_string", "")))
    return _pool


# ---------------------------------------------------------------------------
# Declarative tool routes: path -> tool function + body-key mapping
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ToolRoute:
    """One tool route: a tool function plus its parameter-key mapping.

    ``pool_kw`` is the tool function's pool parameter name (``None`` for
    pool-free operations such as ``release_compare``); ``required`` keys
    must be present, ``optional`` keys pass through when present. The
    tenant id always comes from the body (shared placeholder default).
    ``method`` is ``POST`` (JSON body) or ``GET`` (query parameters); the
    lifecycle read routes (UI SPA Task 5b) are the GET rows.
    """

    path: str
    fn: Callable[..., object]
    pool_kw: str | None
    required: tuple[str, ...]
    optional: tuple[str, ...] = ()
    method: str = "POST"


def _tool_routes() -> tuple[ToolRoute, ...]:
    """The route table (tool modules imported lazily, api/http.py style)."""
    from corpus_kb.api.routes_lifecycle import lifecycle_routes
    from corpus_kb.tools import (
        cycle_tools,
        demo_tools,
        notebook_tools,
        release_tools,
        research_tools,
    )

    return (
        # -- release lifecycle (U5, v6 §6) --------------------------------
        ToolRoute(
            "/api/release/assess",
            release_tools.release_assess,
            "pg_pool",
            ("codebook_version_id",),
            ("profile", "run_id", "tenant_id"),
        ),
        ToolRoute(
            "/api/release/request",
            release_tools.release_request,
            "pg_pool",
            ("codebook_version_id", "creator"),
            ("codebook_id", "profile", "release_id", "tenant_id"),
        ),
        ToolRoute(
            "/api/release/review",
            release_tools.release_review,
            "pg_pool",
            ("codebook_version_id", "release_id"),
            ("tenant_id",),
        ),
        ToolRoute(
            "/api/release/release",
            release_tools.release_execute,
            "pg_pool",
            ("codebook_version_id", "release_id", "approver"),
            ("run_id", "tenant_id", "waivers"),
        ),
        ToolRoute(
            "/api/release/status",
            release_tools.release_status,
            "pg_pool",
            ("codebook_version_id",),
            ("tenant_id",),
            "GET",
        ),
        ToolRoute(
            "/api/release/compare",
            release_tools.release_compare,
            None,
            ("codebook_version_id", "old_release_id", "new_release_id"),
            ("tenant_id",),
        ),
        # -- pre-existing research/coding surfaces (P7 mechanical sweep) --
        ToolRoute(
            "/api/research/ingest-transcript",
            research_tools.research_ingest_transcript,
            "pg_pool",
            ("file_path",),
            ("project_id", "tenant_id", "title", "force", "validate"),
        ),
        ToolRoute(
            "/api/research/ingest",
            research_tools.research_ingest,
            "pg_pool",
            ("path",),
            ("project_id", "tenant_id", "force", "validate"),
        ),
        ToolRoute(
            "/api/coding/run",
            research_tools.coding_run,
            "pool",
            ("codebook_version_id",),
            ("project_id", "tenant_id", "cal_alpha"),
        ),
        ToolRoute(
            "/api/coding/inductive",
            research_tools.inductive_run,
            "pool",
            (),
            ("project_id", "tenant_id"),
        ),
        ToolRoute(
            "/api/codebook/promote",
            research_tools.codebook_promote,
            "pool",
            ("proposed_id", "name", "definition"),
            ("exclusion", "inclusion", "label", "tau_dup", "tenant_id"),
        ),
        ToolRoute(
            "/api/research/report",
            research_tools.research_report,
            "pool",
            (),
            ("codebook_version_id", "level", "project_id", "tenant_id"),
        ),
        ToolRoute(
            "/api/review/execute",
            research_tools.review_execute,
            "pool",
            ("assignment_id", "decision", "reviewer"),
            ("note", "tenant_id"),
        ),
        ToolRoute(
            "/api/notebook/ask",
            notebook_tools.notebook_ask_tool,
            "pg_pool",
            ("project_id", "question"),
            (
                "code",
                "doc_ids",
                "k",
                "min_confidence",
                "retrieval_only",
                "review_status",
                "source_type",
                "speaker_role",
                "tenant_id",
                "topic_id",
            ),
        ),
        ToolRoute(
            "/api/notebook/evidence",
            notebook_tools.notebook_evidence_tool,
            "pg_pool",
            ("code",),
            ("limit", "project_id", "tenant_id", "version_id"),
        ),
        ToolRoute(
            "/api/notebook/uncoded",
            notebook_tools.notebook_uncoded_tool,
            "pg_pool",
            (),
            ("k", "project_id", "tenant_id", "version_id"),
        ),
        ToolRoute(
            "/api/notebook/overlap",
            notebook_tools.notebook_overlap_tool,
            "pg_pool",
            (),
            ("project_id", "tenant_id", "version_id"),
        ),
        # -- full surface parity wave: the long-running orchestrators -----
        # Synchronous POSTs, exactly like the CLI: one call runs the whole
        # stage/cycle and returns the receipt when it ends. The cycle's
        # codebook_promotion gate stays hard-floored (a human promotes).
        ToolRoute(
            "/api/research/cycle",
            cycle_tools.research_cycle,
            "pg_pool",
            (),
            (
                "approve",
                "codebook_version_id",
                "guide",
                "halt_on",
                "ingest_dir",
                "mode",
                "project_id",
                "question",
                "tenant_id",
            ),
        ),
        ToolRoute(
            "/api/research/demo",
            demo_tools.research_demo,
            None,
            (),
            (),
        ),
        # -- lifecycle read routes (UI SPA Task 5b, spec B2): the GET rows --
        *lifecycle_routes(),
    )


def tool_routes() -> tuple[ToolRoute, ...]:
    """The public route table (the socket arms build from the same rows)."""
    return _tool_routes()


def _route_by_path() -> dict[str, ToolRoute]:
    return {route.path: route for route in _tool_routes()}


async def _respond(fn: Callable[..., object], kwargs: dict[str, Any]) -> JSONResponse:
    """Call the shared tool function and map outcome -> honest status code."""
    try:
        result = fn(**kwargs)
        payload = await result if inspect.isawaitable(result) else result
    except Exception as exc:
        return error_response(exc)
    body = payload if isinstance(payload, dict) else {}
    status = body.get("status")
    if status == "blocked":
        # D3 parity on HTTP: a refused release is a structured refusal.
        return JSONResponse(body, status_code=409)
    if status == "unavailable":
        # Pre-migration database / missing extra: retryable, not a bug.
        return JSONResponse(body, status_code=503)
    if status == "error":
        error_type = str(body.get("error_type") or "")
        code = error_status(error_type) if error_type else 400
        return JSONResponse(body, status_code=code)
    return JSONResponse(body)


def _post_handler(route: ToolRoute) -> Callable[[Request], Awaitable[JSONResponse]]:
    async def handler(request: Request) -> JSONResponse:
        body = await parse_body(request)
        missing = [key for key in route.required if key not in body]
        if missing:
            return error_response(
                "validation_error",
                f"missing required fields: {missing}",
            )
        kwargs: dict[str, Any] = {key: body[key] for key in route.required}
        for key in route.optional:
            if key in body:
                kwargs[key] = body[key]
        if route.pool_kw is not None:
            kwargs[route.pool_kw] = await research_pool()
        return await _respond(route.fn, kwargs)

    return handler


async def release_status_get(request: Request) -> JSONResponse:
    """GET /api/release/status — the D1 status view for one codebook version."""
    from corpus_kb.tools import release_tools

    codebook_version_id = request.query_params.get("codebook_version_id")
    if not codebook_version_id:
        return error_response(
            "validation_error",
            "missing required field: codebook_version_id",
        )
    return await _respond(
        release_tools.release_status,
        {
            "pg_pool": await research_pool(),
            "codebook_version_id": codebook_version_id,
            "tenant_id": request.query_params.get("tenant_id") or DEFAULT_TENANT_ID,
        },
    )


def _get_handler(route: ToolRoute) -> Callable[[Request], Awaitable[JSONResponse]]:
    """One GET route adapter: query parameters in, house envelope out.

    Values coerce by the tool function's own annotations (ints for counts),
    so the browser, the socket arm, and the MCP schema agree on types.
    """

    async def handler(request: Request) -> JSONResponse:
        params = dict(request.query_params)
        missing = [key for key in route.required if key not in params]
        if missing:
            return error_response("validation_error", f"missing required fields: {missing}")
        annotations = get_type_hints(route.fn)
        kwargs: dict[str, Any] = {}
        for key in (*route.required, *route.optional):
            if key not in params:
                continue
            raw: object = params[key]
            if annotations.get(key) is int:
                raw = opt_int(raw, 0)
            kwargs[key] = raw
        if route.pool_kw is not None:
            kwargs[route.pool_kw] = await research_pool()
        if "tenant_id" in inspect.signature(route.fn).parameters:
            kwargs.setdefault("tenant_id", DEFAULT_TENANT_ID)
        return await _respond(route.fn, kwargs)

    return handler


def research_routes() -> list[Route]:
    """The research + release routes for the main Starlette app."""
    table = _route_by_path()
    routes: list[Route] = []
    for route in _tool_routes():
        if route.method == "GET":
            handler = (
                release_status_get if route.path == "/api/release/status" else _get_handler(route)
            )
            routes.append(Route(route.path, handler, methods=["GET"]))
            continue
        routes.append(Route(route.path, _post_handler(route), methods=["POST"]))
    assert len(table) == len(routes), "route table collision"
    return routes
