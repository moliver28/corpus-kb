"""MCP/HTTP/socket/CLI handler for ``analysis_embed_meta`` (W6).

The ONE meta-embedding build path: build the requested aggregate kinds
(document, topic, belief_summary, profile) and upsert them. Lazy
imports keep module load light (the P0 rule). The envelope is honest
about skips: empty corpora, missing topics, and an unavailable embedder
are notes, never silent zeros.
"""

from __future__ import annotations

import asyncpg

from ..errors import ValidationError
from ..utils.config_sections import mapping_section
from .ingest_common import DEFAULT_TENANT_ID

__all__ = ["analysis_embed_meta"]

META_KINDS: frozenset[str] = frozenset({"document", "topic", "belief_summary", "profile"})


def _parse_kinds(kinds: str | None) -> list[str]:
    """CSV kinds (default: all four, sorted), validated against the set."""
    all_kinds = sorted(META_KINDS)
    if kinds is None or not kinds.strip() or kinds.strip() == "all":
        return all_kinds
    parsed = [part.strip() for part in kinds.split(",") if part.strip()]
    if not parsed:
        return all_kinds
    unknown = [kind for kind in parsed if kind not in META_KINDS]
    if unknown:
        raise ValidationError(
            f"Unknown meta kinds: {', '.join(unknown)}.",
            remediation=f"Use any of: {', '.join(all_kinds)} (or 'all').",
        )
    return sorted(set(parsed))


async def analysis_embed_meta(
    pg_pool: asyncpg.Pool,
    config: dict[str, object] | None = None,
    tenant_id: str = DEFAULT_TENANT_ID,
    *,
    kinds: str | None = None,
) -> dict[str, object]:
    """Build and upsert the coarse corpus aggregates.

    Returns ``{status, kinds, built, per_kind, notes}`` — deterministic
    order (kind asc, key asc), stable for harness snapshots.
    """
    from ..analysis.meta_build import build_meta_rows
    from ..analysis.meta_store import upsert_meta_rows

    requested = _parse_kinds(kinds)
    cfg = dict(config) if isinstance(config, dict) else {}
    rows, notes = await build_meta_rows(pg_pool, tenant_id, requested, cfg)
    is_duckdb = str(mapping_section(cfg, "database").get("backend") or "postgres") == "duckdb"
    written = await upsert_meta_rows(pg_pool, tenant_id, rows, is_duckdb=is_duckdb)
    per_kind: dict[str, int] = dict.fromkeys(requested, 0)
    for row in rows:
        per_kind[row.meta_kind] = per_kind.get(row.meta_kind, 0) + 1
    return {
        "status": "success",
        "kinds": requested,
        "built": written,
        "per_kind": per_kind,
        "notes": notes,
    }
