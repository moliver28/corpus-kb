"""Ingest route family (zero-drift split of api/http.py).

POST /api/ingest/file, /api/ingest/text, /api/ingest/directory, /api/ingest/url
POST /api/ingest/upload                       (UI SPA Task 5a, multipart)
GET  /api/ingest/status
POST /api/ingest/reembed

Thin adapters: parse JSON, call the shared tool/handler layer, return
JSONResponse. Every route runs through ``@route_errors`` (api._body) —
ONE error path. Handler imports stay inside the route bodies so tests can
monkeypatch the handler-module singletons.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from starlette.datastructures import UploadFile
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.types import Message, Receive

from corpus_kb.api._body import DEFAULT_TENANT_ID, opt_bool, opt_str, parse_body, route_errors
from corpus_kb.domain.models import (
    IngestDirectoryCommand,
    IngestFileCommand,
    IngestTextCommand,
)
from corpus_kb.errors import PayloadTooLargeError, ValidationError
from corpus_kb.tools.ingest_upload_tools import BYTES_PER_MB


def _counting_receive(receive: Receive, max_mb: int) -> Receive:
    """Wrap one receive channel; abort the body read past the size cap.

    Every body chunk is counted as it arrives, so a chunked or
    lying-header client hits the typed 413 DURING form parsing — before
    the parser can spool an unbounded body to a temp file.
    """
    cap_bytes = max_mb * BYTES_PER_MB
    received = 0

    async def wrapped() -> Message:
        nonlocal received
        message = await receive()
        if message["type"] == "http.request":
            received += len(message.get("body", b""))
            if received > cap_bytes:
                raise PayloadTooLargeError(f"The request body is over the {max_mb} MB upload cap.")
        return message

    return wrapped


def _declared_content_length(request: Request) -> int | None:
    """The declared body size; junk or duplicated headers are a typed 400.

    A missing header is fine (chunked upload). Anything unparsable is a
    client fault, so it maps to validation_error — never a bare 500.
    """
    values = request.headers.getlist("content-length")
    if not values:
        return None
    if len(values) > 1:
        raise ValidationError("The request has more than one Content-Length header.")
    try:
        return int(values[0])
    except ValueError:
        raise ValidationError("The Content-Length header is not a number.") from None


@route_errors
async def ingest_file(request: Request) -> JSONResponse:
    """POST /api/ingest/file — ingest a file from disk."""
    from corpus_kb.handlers.command_handler import get_command_handler

    body = await parse_body(request)
    cmd = IngestFileCommand(
        tenant_id=UUID(body.get("tenant_id", "00000000-0000-0000-0000-000000000001")),
        file_path=body["file_path"],
        content=body.get("content"),
        source_type=body.get("source_type"),
        validate_mode=opt_str(body.get("validate")),
    )
    handler = get_command_handler()
    result = await handler.handle_ingest_file(cmd)
    return JSONResponse(result)


@route_errors
async def ingest_text(request: Request) -> JSONResponse:
    """POST /api/ingest/text — ingest raw text."""
    from corpus_kb.handlers.command_handler import get_command_handler

    body = await parse_body(request)
    cmd = IngestTextCommand(
        tenant_id=UUID(body.get("tenant_id", "00000000-0000-0000-0000-000000000001")),
        text=body["text"],
        source=body.get("source", "raw_text"),
        source_type=body.get("source_type", "text"),
        validate_mode=opt_str(body.get("validate")),
    )
    handler = get_command_handler()
    result = await handler.handle_ingest_text(cmd)
    return JSONResponse(result)


@route_errors
async def ingest_directory(request: Request) -> JSONResponse:
    """POST /api/ingest/directory — ingest all files in a directory."""
    from corpus_kb.handlers.command_handler import get_command_handler

    body = await parse_body(request)
    cmd = IngestDirectoryCommand(
        tenant_id=UUID(body.get("tenant_id", "00000000-0000-0000-0000-000000000001")),
        directory_path=body["directory_path"],
        recursive=body.get("recursive", True),
        validate_mode=opt_str(body.get("validate")),
    )
    handler = get_command_handler()
    result = await handler.handle_ingest_directory(cmd)
    return JSONResponse(result)


@route_errors
async def ingest_url_route(request: Request) -> JSONResponse:
    """POST /api/ingest/url — fetch (or crawl) a website and ingest its pages."""
    from corpus_kb.api.research_http import research_pool
    from corpus_kb.tools.ingest_tools import ingest_url

    body: dict[str, Any] = await parse_body(request)
    result = await ingest_url(
        str(body["url"]),
        await research_pool(),
        tenant_id=body.get("tenant_id", "00000000-0000-0000-0000-000000000001"),
        crawl=opt_bool(body.get("crawl"), False),
        source_type=body.get("source_type"),
        validate=opt_str(body.get("validate")),
    )
    return JSONResponse(result)


@route_errors
async def ingest_upload_route(request: Request) -> JSONResponse:
    """POST /api/ingest/upload — one multipart file through the standard pipeline."""
    from corpus_kb.api.research_http import research_pool
    from corpus_kb.tools.ingest_upload_tools import (
        enforce_request_caps,
        ingest_upload,
        require_multipart_support,
        upload_cap_mb,
    )

    require_multipart_support()
    max_mb = upload_cap_mb(None)
    # The Content-Length check is only the fast path. The wrapped receive
    # below counts every byte DURING form parsing, so a chunked or lying
    # header cannot spool an unbounded body to disk before the 413.
    raw_length = _declared_content_length(request)
    enforce_request_caps(
        request.headers.get("content-type"),
        raw_length,
        max_mb,
    )
    counting = Request(
        request.scope,
        receive=_counting_receive(request.receive, max_mb),
    )
    try:
        form = await counting.form()
    except StarletteHTTPException as exc:
        # starlette wraps malformed or truncated multipart in a bare 400.
        # That is a client fault: the taxonomy answers validation_error.
        if exc.status_code != 400:
            raise
        raise ValidationError("The multipart form body is malformed or cut short.") from exc
    upload = form.get("file")
    if not isinstance(upload, UploadFile):
        raise ValidationError("The upload needs a multipart file field named file.")
    source_type = form.get("source_type")
    result = await ingest_upload(
        upload,
        await research_pool(),
        tenant_id=str(form.get("tenant_id", DEFAULT_TENANT_ID)),
        source_type=str(source_type) if source_type is not None else None,
        max_upload_mb=max_mb,
    )
    return JSONResponse(result)


@route_errors
async def ingest_status_route(request: Request) -> JSONResponse:
    """GET /api/ingest/status — recent ingest_runs ledger entries + degraded counts."""
    from corpus_kb.api.research_http import research_pool
    from corpus_kb.tools import ingest_surface_tools

    limit = int(request.query_params.get("limit", ingest_surface_tools.DEFAULT_RUN_LIMIT))
    result = await ingest_surface_tools.ingest_status(
        await research_pool(),
        tenant_id=request.query_params.get("tenant_id", "00000000-0000-0000-0000-000000000001"),
        limit=limit,
    )
    status_code = 503 if result.get("status") == "unavailable" else 200
    return JSONResponse(result, status_code=status_code)


@route_errors
async def ingest_reembed_route(request: Request) -> JSONResponse:
    """POST /api/ingest/reembed — re-embed degraded chunks (honest summary)."""
    from corpus_kb.api.research_http import research_pool
    from corpus_kb.tools import ingest_surface_tools

    body = await parse_body(request)
    raw_limit = body.get("limit")
    result = await ingest_surface_tools.ingest_reembed(
        await research_pool(),
        tenant_id=body.get("tenant_id", "00000000-0000-0000-0000-000000000001"),
        limit=int(raw_limit) if raw_limit is not None else None,
        doc_id=body.get("doc_id"),
    )
    # Same contract as ingest-status: a pre-migration database is 503,
    # never a silent 200.
    status_code = 503 if result.get("status") == "unavailable" else 200
    return JSONResponse(result, status_code=status_code)
