"""Shared row-mapping helpers for the domain stores (issue #233).

The five small domain stores (labeling, topics, beliefs, conflicts,
statements) repeat the same row glue: one jsonb string parse, one
entity-name unfold, one timestamp ISO fold. This module holds the one
copy of each. Every store keeps its own SQL, its own tenant scope
(:func:`corpus_kb.storage.tenant_conn.tenant_connection`), and its own
public API.
"""

from __future__ import annotations

import json
from datetime import datetime

__all__ = ["entity_names", "iso_or_none", "parse_jsonb"]


def parse_jsonb(raw: object) -> object:
    """asyncpg hands jsonb back as a string; parse it, keep raw on failure."""
    if not isinstance(raw, str):
        return raw
    try:
        return json.loads(raw)
    except ValueError:
        return raw


def entity_names(raw: object) -> list[str]:
    """Names out of a statements entities value (a list of dicts)."""
    entities = json.loads(raw) if isinstance(raw, str) else raw
    if not isinstance(entities, list):
        return []
    return [e.get("name", "") for e in entities if isinstance(e, dict)]


def iso_or_none(value: datetime | None) -> str | None:
    """ISO text for a timestamp value, or None for NULL."""
    return value.isoformat() if value else None
