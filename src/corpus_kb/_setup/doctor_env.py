"""Configuration-truth checks for ``corpus-kb doctor`` (zero-drift Wave 4C).

Two checks, same contract as the research checks (read-only, never flip the
doctor exit code):

* :func:`env_var_check` — warns about CORPUS_KB_* variables set in the
  environment that this version does not read. Pure and offline: it runs
  even when Postgres is unreachable.
* :func:`dimension_guard_check` — compares the configured
  ``embedding.dimensions`` with the actual ``chunks_vectors.vector``
  typmod and reports the stored per-row dimensions histogram. pgvector
  enforces the exact typmod width, so a mismatch means EVERY vector
  insert fails; the check fails with the config fix.
"""

from __future__ import annotations

import os
from typing import Any

import asyncpg

from corpus_kb._setup.doctor_research import (
    DOC_GETTING_STARTED,
    STATUS_FAIL,
    STATUS_OK,
    STATUS_WARN,
    ResearchCheck,
)
from corpus_kb.config import KNOWN_CORPUS_KB_ENV_VARS

DOC_ADMIN = "docs/ADMIN.md"

SETUP_FIX = "corpus-kb setup"

# Empirical contract (pgvector 0.8.3, Postgres 16): INSERT of a 768-dim
# vector into a vector(4096) column fails with SQLSTATE 22000 ("expected
# 4096 dimensions, not 768"). pgvector never pads and never truncates.
# atttypmod for a vector(N) column is exactly N (and -1 when un-typmodded).
VECTOR_TYPEMOD_SQL = (
    "SELECT format_type(a.atttypid, a.atttypmod) AS typ, a.atttypmod AS width "
    "FROM pg_attribute a "
    "WHERE a.attrelid = 'public.chunks_vectors'::regclass AND a.attname = 'vector'"
)
DIMENSIONS_HISTOGRAM_SQL = (
    "SELECT dimensions, count(*) AS n FROM chunks_vectors GROUP BY dimensions ORDER BY dimensions"
)

DEFAULT_EMBEDDING_DIMENSIONS = 768
DEFAULT_EMBEDDING_MODEL = "nomic-embed-text"


def unknown_env_vars(environ: dict[str, str] | None = None) -> list[str]:
    """CORPUS_KB_* names set in the environment that config.py does not map.

    The known set also covers the two variables read outside the override
    map (config discovery, snapshot interval), so only genuinely unknown
    names surface here.
    """
    env = os.environ if environ is None else environ
    return sorted(
        name
        for name in env
        if name.startswith("CORPUS_KB_") and name not in KNOWN_CORPUS_KB_ENV_VARS
    )


def env_var_check(environ: dict[str, str] | None = None) -> ResearchCheck:
    """Warn about set-but-unread CORPUS_KB_* variables (config-rot tripwire)."""
    unknown = unknown_env_vars(environ)
    if not unknown:
        return ResearchCheck(
            name="CORPUS_KB env vars",
            status=STATUS_OK,
            detail=(
                f"no unknown variables set ({len(KNOWN_CORPUS_KB_ENV_VARS)} variables "
                "are read by this version)"
            ),
            doc=DOC_ADMIN,
        )
    return ResearchCheck(
        name="CORPUS_KB env vars",
        status=STATUS_WARN,
        detail=(
            f"{len(unknown)} variable(s) set but NOT read by this version: {', '.join(unknown)}"
        ),
        fix="Remove the variable, or set the matching key in config.yaml (see docs/ADMIN.md)",
        doc=DOC_ADMIN,
    )


def _configured_dimensions(config: dict[str, Any]) -> tuple[int, str]:
    """The configured (dimensions, model) pair with safe fallbacks."""
    emb_cfg = config.get("embedding") or {}
    if not isinstance(emb_cfg, dict):
        return DEFAULT_EMBEDDING_DIMENSIONS, DEFAULT_EMBEDDING_MODEL
    raw_dims = emb_cfg.get("dimensions", DEFAULT_EMBEDDING_DIMENSIONS)
    try:
        dimensions = int(raw_dims)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        dimensions = 0
    model = str(emb_cfg.get("model", DEFAULT_EMBEDDING_MODEL))
    return dimensions, model


async def dimension_guard_check(conn: asyncpg.Connection, config: dict[str, Any]) -> ResearchCheck:
    """Configured embedding dimensions must match the vector column typmod.

    Reads the catalog (no RLS applies to pg_attribute) for the column type,
    then reads the stored per-row dimensions histogram inside a tenant-GUC
    transaction (FORCE RLS makes a GUC-less read return zero rows).
    """
    name = "embedding dimensions vs schema"
    dimensions, model = _configured_dimensions(config)
    row = await conn.fetchrow(VECTOR_TYPEMOD_SQL)
    if row is None:
        return ResearchCheck(
            name=name,
            status=STATUS_WARN,
            detail="chunks_vectors.vector not found (migrations not applied)",
            fix=SETUP_FIX,
            doc=DOC_GETTING_STARTED,
        )
    width = int(row["width"])
    typ = str(row["typ"])
    if width < 0:
        # Un-typmodded vector column: pgvector accepts any width; the
        # per-row dimensions column stays the only honesty marker.
        return ResearchCheck(
            name=name,
            status=STATUS_WARN,
            detail=f"vector column {typ} has no fixed width; inserts accept any dimensions",
            fix="re-create the column with an exact typmod (see src/corpus_kb/storage/schema.sql)",
            doc=DOC_ADMIN,
        )
    if dimensions != width:
        return ResearchCheck(
            name=name,
            status=STATUS_FAIL,
            detail=(
                f"embedding.dimensions={dimensions} (model {model!r}) but the column is "
                f"{typ}. pgvector needs an exact match: every vector insert will fail."
            ),
            fix=(
                f"Set embedding.model to a model that emits {width} dimensions "
                "(example: qwen3-embedding:8b-q8_0), set embedding.dimensions to "
                f"{width}, then re-ingest."
            ),
            doc=DOC_ADMIN,
        )
    histogram = await _dimensions_histogram(conn, width)
    stale = {dim: n for dim, n in histogram.items() if dim is not None and dim != width}
    if stale:
        stale_txt = ", ".join(f"{dim}: {n} row(s)" for dim, n in sorted(stale.items()))
        return ResearchCheck(
            name=name,
            status=STATUS_WARN,
            detail=(
                f"configured {dimensions} matches {typ}; stored rows at other widths: "
                f"{stale_txt} (stale rows are invisible to a width-matched search)"
            ),
            fix="run: corpus-kb ingest --reembed",
            doc=DOC_ADMIN,
        )
    hist_txt = (
        ", ".join(f"{dim}: {n}" for dim, n in sorted(histogram.items(), key=str))
        or "no vectors stored"
    )
    return ResearchCheck(
        name=name,
        status=STATUS_OK,
        detail=f"configured {dimensions} matches {typ}; stored dimensions ({hist_txt})",
        doc=DOC_ADMIN,
    )


async def _dimensions_histogram(conn: asyncpg.Connection, width: int) -> dict[int | None, int]:
    """Per-row ``dimensions`` histogram; unreadable tables return {} gracefully."""
    try:
        async with conn.transaction():
            await conn.execute(
                "SELECT set_config('app.current_tenant_id', $1, true)",
                "00000000-0000-0000-0000-000000000001",
            )
            rows = await conn.fetch(DIMENSIONS_HISTOGRAM_SQL)
    except Exception:
        # The histogram is bonus context; a failed read must never break doctor.
        return {width: 0}
    return {
        (int(r["dimensions"]) if r["dimensions"] is not None else None): int(r["n"]) for r in rows
    }


__all__ = [
    "dimension_guard_check",
    "env_var_check",
    "unknown_env_vars",
]
