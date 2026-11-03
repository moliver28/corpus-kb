"""Shared UUID input guard for MCP tool functions (issue 229 wave 1).

MCP tool functions take ids as JSON strings. A malformed id used to
escape as the stdlib ``ValueError`` from ``UUID()``; this guard raises
the taxonomy's ``ValidationError`` instead, so every surface answers
with the honest 400 / -32602 / exit-1 triple.
"""

from __future__ import annotations

from uuid import UUID

from corpus_kb.errors import ValidationError

__all__ = ["uuid_arg"]


def uuid_arg(value: str, field: str) -> UUID:
    """Parse one caller-supplied id string, or raise the typed error."""
    try:
        return UUID(value)
    except ValueError as exc:
        raise ValidationError(f"{field} is not a valid UUID: {value!r}") from exc
