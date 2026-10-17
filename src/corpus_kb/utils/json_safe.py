"""JSON-safe conversion for database row values.

asyncpg returns ``uuid.UUID``, ``datetime``, and ``Decimal`` objects that
``json.dumps`` (and therefore ``JSONResponse``) cannot serialize. Wire
boundaries convert rows through :func:`json_safe` so every surface emits
the same JSON for the same rows.
"""

from __future__ import annotations

from datetime import date, datetime, time
from decimal import Decimal
from typing import Any
from uuid import UUID


def json_safe(value: Any) -> Any:
    """Return value as JSON-serializable, converting row scalars to strings."""
    if isinstance(value, (UUID, datetime, date, time, Decimal)):
        return str(value)
    if isinstance(value, dict):
        return {str(key): json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [json_safe(item) for item in value]
    if isinstance(value, (bytes, bytearray, memoryview)):
        return bytes(value).hex()
    return value
