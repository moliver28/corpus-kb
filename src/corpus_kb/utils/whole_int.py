"""One whole-number coercion for caller-supplied counts.

Both the HTTP/socket ``opt_int`` (api._body) and the tool-layer window
guards (tools._count_input) coerce JSON counts the SAME way: whole
numbers, whole floats (``5.0``), and digit strings pass; everything
else raises the taxonomy ``ValidationError``, so every surface answers
its one typed failure (HTTP 400, socket -32602, CLI exit 1) instead of
a silent default or a database bind error. utils is a shared leaf, so
api and tools import it without a layering bend.
"""

from __future__ import annotations

from corpus_kb.errors import ValidationError

__all__ = ["INT8_MAX", "INT8_MIN", "coerce_whole_int"]

# Postgres int8 bind range: a count past this fails the asyncpg bind one
# layer down (500/-32603), so the guards refuse it up front with the
# caller's failure class (issue #238 follow-up, deliberate).
INT8_MAX = 9223372036854775807
INT8_MIN = -9223372036854775808


def coerce_whole_int(raw: object, default: int | None, *, name: str = "value") -> int | None:
    """Coerce one JSON count. Absent answers the default; junk raises.

    Whole numbers, whole floats, and digit strings pass. Any other value
    (``"abc"``, ``5.7``, ``true``, a list) raises the taxonomy
    ``ValidationError``. No silent fallback — issue #238 moved the old
    fall-back-to-default behavior here so every surface fails the same
    way on the same bad input.
    """
    if raw is None:
        return default
    if isinstance(raw, bool):
        raise ValidationError(f"{name} must be a whole number.")
    if isinstance(raw, int):
        return raw
    if isinstance(raw, float):
        if not raw.is_integer():
            raise ValidationError(f"{name} must be a whole number.")
        return int(raw)
    if isinstance(raw, str):
        try:
            return int(raw)
        except ValueError:
            raise ValidationError(f"{name} must be a whole number.") from None
    raise ValidationError(f"{name} must be a whole number.")
