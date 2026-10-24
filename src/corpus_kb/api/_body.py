"""Shared HTTP adapter plumbing (thin-surface helpers).

One definition for every route module under ``api/``: the Starlette
adapters parse JSON, default the tenant, and answer every failure through
ONE error helper (zero-drift rule 4 — no second error envelope, no
catch-all 400). Kept dependency-light so both ``http.py`` and
``research_http.py`` import it without cycles.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from functools import wraps
from typing import Any

from starlette.requests import Request
from starlette.responses import JSONResponse

from corpus_kb.errors import (
    DEFAULT_ERROR_TYPE,
    DEFAULT_HTTP_STATUS,
    ERROR_MAP,
    CorpusError,
    error_status,
)

logger = logging.getLogger(__name__)

DEFAULT_TENANT_ID = "00000000-0000-0000-0000-000000000001"


async def parse_body(request: Request) -> dict[str, Any]:
    """Parse the JSON body, returning an empty dict on failure."""
    try:
        data = await request.json()
        if isinstance(data, dict):
            return data
        return {}
    except Exception:
        return {}


def opt_int(raw: object, default: int) -> int:
    """Coerce one JSON count. Absent or junk answers the default."""
    try:
        return int(raw) if raw is not None else default
    except (TypeError, ValueError):
        return default


def opt_bool(raw: object, default: bool) -> bool:
    """Coerce one JSON flag. Real bools pass; strings parse; junk defaults."""
    if raw is None:
        return default
    if isinstance(raw, bool):
        return raw
    if isinstance(raw, str):
        lowered = raw.strip().lower()
        if lowered in ("true", "1", "yes", "on"):
            return True
        if lowered in ("false", "0", "no", "off", ""):
            return False
        return default
    if isinstance(raw, (int, float)):
        return bool(raw)
    return default


def error_response(
    exc_or_type: BaseException | str,
    message: str | None = None,
) -> JSONResponse:
    """Build the ONE error envelope: ``{status, error, error_type}``.

    A ``CorpusError`` answers with its own status and taxonomy name; a
    known exception class name (or ``error_type`` string) answers through
    ``ERROR_MAP``; anything else is an internal error (500, logged). The
    message travels in ``error`` unchanged.
    """
    if isinstance(exc_or_type, CorpusError):
        return JSONResponse(
            {
                "status": "error",
                "error": str(exc_or_type),
                "error_type": exc_or_type.error_type,
            },
            status_code=exc_or_type.http_status,
        )
    if isinstance(exc_or_type, BaseException):
        name = type(exc_or_type).__name__
        mapped = ERROR_MAP.get(name)
        if mapped is None:
            logger.error("Unhandled route exception: %s", exc_or_type, exc_info=exc_or_type)
            return JSONResponse(
                {
                    "status": "error",
                    "error": str(exc_or_type),
                    "error_type": DEFAULT_ERROR_TYPE,
                },
                status_code=DEFAULT_HTTP_STATUS,
            )
        return JSONResponse(
            {"status": "error", "error": str(exc_or_type), "error_type": name},
            status_code=mapped[0],
        )
    error_type = str(exc_or_type)
    return JSONResponse(
        {
            "status": "error",
            "error": message if message is not None else error_type,
            "error_type": error_type,
        },
        status_code=error_status(error_type),
    )


def route_errors(
    fn: Callable[[Request], Awaitable[JSONResponse]],
) -> Callable[[Request], Awaitable[JSONResponse]]:
    """Run one route through the single error path (zero-drift rule 4)."""

    @wraps(fn)
    async def wrapper(request: Request) -> JSONResponse:
        try:
            return await fn(request)
        except Exception as exc:
            return error_response(exc)

    return wrapper
