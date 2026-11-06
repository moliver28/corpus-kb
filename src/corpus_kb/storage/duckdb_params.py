"""asyncpg-SQL onto the DuckDB mirror, at the gateway choke point.

Postgres-flavored SQL that reaches :class:`~corpus_kb.storage.gateway.DuckDbGateway`
keeps its asyncpg casts and its ``uuid.UUID`` binds. Two shims translate at
ONE place (program rule 1 — no per-call-site copies):

- :func:`map_pg_casts` rewrites the three cast spellings DuckDB spells
  differently (``::uuid`` -> ``::VARCHAR``, ``::uuid[]`` -> ``::VARCHAR[]``,
  ``::jsonb`` -> ``::JSON``). Text inside string literals is never touched.
- :func:`bind_params` binds every ``uuid.UUID`` (also inside a list) as its
  canonical text — DuckDB VARCHAR columns store uuid text, and a UUID object
  would fail the ``VARCHAR[]`` array binds.

Both run inside the gateway's ``fetch``/``execute``/``executemany`` and
``write_batch``; callers keep writing Postgres SQL unchanged. Pure functions,
no connection state; the cast map is ``lru_cache``-d because the same
statements repeat on every call.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from functools import lru_cache
from typing import Any
from uuid import UUID

# Longest match first: ::uuid[] must fold before ::uuid.
_CAST_RULES: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"::uuid\[\]", re.IGNORECASE), "::VARCHAR[]"),
    (re.compile(r"::uuid(?![a-z0-9_[])", re.IGNORECASE), "::VARCHAR"),
    (re.compile(r"::jsonb(?![a-z0-9_[])", re.IGNORECASE), "::JSON"),
)


def _mask_literals(sql: str) -> list[bool]:
    """Per-character keep-mask: False inside single/double-quoted regions.

    A doubled quote is an escape inside the region, never its end. An
    unterminated literal consumes the tail (the cast rewrite is best-effort
    on broken SQL — the driver reports the parse error either way).
    """
    keep = [True] * len(sql)
    quote = ""
    i = 0
    while i < len(sql):
        ch = sql[i]
        if quote:
            keep[i] = False
            if ch == quote:
                if i + 1 < len(sql) and sql[i + 1] == quote:
                    keep[i + 1] = False
                    i += 2
                    continue
                quote = ""
        elif ch in ("'", '"'):
            quote = ch
            keep[i] = False
        i += 1
    return keep


@lru_cache(maxsize=512)
def _map_cached(sql: str) -> str:
    keep = _mask_literals(sql)
    edits: list[tuple[int, int, str]] = []
    for pattern, repl in _CAST_RULES:
        for match in pattern.finditer(sql):
            span = match.span()
            if all(keep[span[0] : span[1]]):
                edits.append((span[0], span[1], repl))
    if not edits:
        return sql
    edits.sort()
    pieces: list[str] = []
    cursor = 0
    for start, end, repl in edits:
        if start < cursor:
            continue  # overlapping match (cannot happen across these rules)
        pieces.append(sql[cursor:start])
        pieces.append(repl)
        cursor = end
    pieces.append(sql[cursor:])
    return "".join(pieces)


def map_pg_casts(sql: str) -> str:
    """Rewrite the asyncpg cast spellings onto the DuckDB mirror's types.

    Deterministic and pure; identical SQL maps to identical SQL, so
    harnesses can snapshot statements. Literal regions are opaque.
    """
    return _map_cached(sql)


def bind_params(args: Sequence[Any]) -> list[Any]:
    """Bind-ready parameters: every ``uuid.UUID`` becomes canonical text.

    Elements inside list or tuple binds (``= ANY($n::text[])`` style and
    array columns) map too — a UUID object inside a ``VARCHAR[]`` bind
    fails the driver's type inference.
    """

    def one(value: Any) -> Any:
        if isinstance(value, UUID):
            return str(value)
        if isinstance(value, (list, tuple)):
            return [one(item) for item in value]
        return value

    return [one(arg) for arg in args]
