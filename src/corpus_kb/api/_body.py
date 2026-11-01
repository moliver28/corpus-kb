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
from math import isfinite
from typing import Any, overload

from starlette.requests import Request
from starlette.responses import JSONResponse

# Re-export of the canonical tenant sentinel (corpus_kb.contracts.model):
# route modules import it from here, so the public name stays stable.
from corpus_kb.contracts.model import DEFAULT_TENANT_ID as DEFAULT_TENANT_ID
from corpus_kb.errors import (
    DEFAULT_ERROR_TYPE,
    DEFAULT_HTTP_STATUS,
    ERROR_MAP,
    CorpusError,
    ValidationError,
    error_status,
)

logger = logging.getLogger(__name__)


async def parse_body(request: Request) -> dict[str, Any]:
    """Parse the JSON body, returning an empty dict on failure."""
    try:
        data = await request.json()
        if isinstance(data, dict):
            return data
        return {}
    except Exception:
        return {}


# Postgres int8 bind range: a count past this fails the asyncpg bind one
# layer down (500/-32603), so the parse helper refuses it up front with
# the caller's failure class (issue #238 follow-up, deliberate).
_INT8_MAX = 9223372036854775807
_INT8_MIN = -9223372036854775808


@overload
def opt_int(raw: object, default: int, *, name: str, allow_negative: bool = ...) -> int: ...
@overload
def opt_int(raw: object, default: None, *, name: str, allow_negative: bool = ...) -> int | None: ...
def opt_int(
    raw: object, default: int | None, *, name: str = "value", allow_negative: bool = False
) -> int | None:
    """Coerce one JSON count. Absent answers the default; junk raises.

    Whole numbers, whole floats (``5.0``), and digit strings pass. Any
    other value (``"abc"``, ``5.7``, ``true``, a list) raises the
    taxonomy ``ValidationError``: HTTP answers 400, socket answers
    -32602. No silent fallback — issue #238 moved the old
    fall-back-to-default behavior here so every surface fails the same
    way on the same bad input.

    Counts are non-negative: a negative value (``k=-5``) raises too —
    Postgres rejects a negative LIMIT, and the old path surfaced that as
    a 500/-32603 internal error. ``allow_negative=True`` (random seeds)
    keeps the full Postgres int8 range instead; anything past that range
    still raises, because the database bind would fail.
    """
    if raw is None:
        return default
    if isinstance(raw, bool):
        raise ValidationError(f"{name} must be a whole number.")
    if isinstance(raw, int):
        value = raw
    elif isinstance(raw, float):
        if not raw.is_integer():
            raise ValidationError(f"{name} must be a whole number.")
        value = int(raw)
    elif isinstance(raw, str):
        try:
            value = int(raw)
        except ValueError:
            raise ValidationError(f"{name} must be a whole number.") from None
    else:
        raise ValidationError(f"{name} must be a whole number.")
    if allow_negative:
        if not _INT8_MIN <= value <= _INT8_MAX:
            raise ValidationError(f"{name} is out of range.")
    else:
        if value < 0:
            raise ValidationError(f"{name} must not be negative.")
        if value > _INT8_MAX:
            raise ValidationError(f"{name} is out of range.")
    return value


@overload
def opt_float(raw: object, default: float, *, name: str) -> float: ...
@overload
def opt_float(raw: object, default: None, *, name: str) -> float | None: ...
def opt_float(raw: object, default: float | None, *, name: str = "value") -> float | None:
    """Coerce one JSON measure. Absent answers the default; junk raises.

    Numbers and numeric strings pass; bools, other junk, and non-finite
    values (``NaN``, ``Infinity`` — a NaN confidence poisons every
    comparison downstream) raise the taxonomy ``ValidationError`` (same
    failure class as ``opt_int``).
    """
    if raw is None:
        return default
    if isinstance(raw, bool):
        raise ValidationError(f"{name} must be a number.")
    if isinstance(raw, (int, float)):
        value = float(raw)
    elif isinstance(raw, str):
        try:
            value = float(raw)
        except ValueError:
            raise ValidationError(f"{name} must be a number.") from None
    else:
        raise ValidationError(f"{name} must be a number.")
    if not isfinite(value):
        raise ValidationError(f"{name} must be a finite number.")
    return value


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


def opt_str(raw: object) -> str | None:
    """One optional JSON string. Absent or null answers None.

    A real string passes through. Any other value stringifies, so the
    downstream gate answers with its typed error, not a silent default.
    ``None`` means "the caller did not say"; the active profile's
    ``validate`` value then decides (Wave 6).
    """
    if raw is None:
        return None
    return str(raw)


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
