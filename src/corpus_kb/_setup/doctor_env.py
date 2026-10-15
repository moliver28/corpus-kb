"""Configuration-truth checks for ``corpus-kb doctor`` (zero-drift Wave 4C).

Two checks, same contract as the research checks (read-only, never flip the
doctor exit code):

* :func:`env_var_check` — warns about CORPUS_KB_* variables set in the
  environment that this version does not read. Pure and offline: it runs
  even when Postgres is unreachable.
* :func:`dimension_guard_check` — compares the configured
  ``embedding.dimensions`` with the actual ``chunks_vectors.embedding``
  typmod and reports the stored per-row dimensions histogram. pgvector
  enforces the exact typmod width, so a mismatch means EVERY vector
  insert fails; the check fails with the config fix.
* :func:`hnsw_index_check` — compares the configured ``retrieval.hnsw``
  m/ef_construction with the live index options (pg_class.reloptions) and
  WARNS on drift (never fails: the index options are a build-time choice).
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

# Empirical contract (pgvector 0.8.x, Postgres 16): INSERT of a 768-dim
# vector into a halfvec(2560) column fails with SQLSTATE 22000 ("expected
# 2560 dimensions, not 768"). pgvector never pads and never truncates.
# atttypmod for a vector(N)/halfvec(N) column is exactly N (-1 un-typmodded).
VECTOR_TYPEMOD_SQL = (
    "SELECT format_type(a.atttypid, a.atttypmod) AS typ, a.atttypmod AS width "
    "FROM pg_attribute a "
    "WHERE a.attrelid = 'public.chunks_vectors'::regclass AND a.attname = 'embedding'"
)
DIMENSIONS_HISTOGRAM_SQL = (
    "SELECT dimensions, count(*) AS n FROM chunks_vectors GROUP BY dimensions ORDER BY dimensions"
)

# The migration-025 HNSW index and its WITH options (retrieval.hnsw mirrors
# these; drift between config and the live index is a WARN, not a FAIL).
HNSW_INDEX_NAME = "idx_chunks_vectors_hnsw_halfvec2560"
HNSW_RELOPTIONS_SQL = "SELECT reloptions FROM pg_class WHERE relname = $1 AND relkind = 'i'"

DEFAULT_EMBEDDING_DIMENSIONS = 2560
DEFAULT_EMBEDDING_MODEL = "qwen3-embedding:4b"


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
        dimensions = int(raw_dims)
    except (TypeError, ValueError):
        dimensions = 0
    model = str(emb_cfg.get("model", DEFAULT_EMBEDDING_MODEL))
    return dimensions, model


def _is_vector_family(typ: str) -> bool:
    """True for the pgvector column families this check understands."""
    return typ.startswith("vector") or typ.startswith("halfvec")


async def dimension_guard_check(conn: asyncpg.Connection, config: dict[str, Any]) -> ResearchCheck:
    """Configured embedding dimensions must match the embedding column typmod.

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
            detail="chunks_vectors.embedding not found (migrations not applied)",
            fix=SETUP_FIX,
            doc=DOC_GETTING_STARTED,
        )
    width = int(row["width"])
    typ = str(row["typ"])
    if not _is_vector_family(typ):
        # e.g. jsonb on a half-migrated database: the width question is
        # meaningless until the real vector column exists.
        return ResearchCheck(
            name=name,
            status=STATUS_WARN,
            detail=f"chunks_vectors.embedding is {typ}, not a vector column (schema incomplete)",
            fix=SETUP_FIX,
            doc=DOC_GETTING_STARTED,
        )
    if width < 0:
        # Un-typmodded vector column: pgvector accepts any width; the
        # per-row dimensions column stays the only honesty marker.
        return ResearchCheck(
            name=name,
            status=STATUS_WARN,
            detail=f"embedding column {typ} has no fixed width; inserts accept any dimensions",
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
                "(example: qwen3-embedding:4b), set embedding.dimensions to "
                f"{width}, then re-ingest. Any other width needs a column "
                "migration (migration 025 is the current regime)."
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


async def hnsw_index_check(conn: asyncpg.Connection, config: dict[str, Any]) -> ResearchCheck:
    """Configured retrieval.hnsw m/ef_construction vs the live index options.

    A WARN-only cross-check (the research checks never flip the doctor exit
    code): rebuilding an HNSW index is a deliberate operator decision, so
    drift between config and pg_class.reloptions is reported, not failed.
    A missing index is also a WARN — pre-0.7 pgvector installs skip it.
    """
    name = "retrieval.hnsw vs live index"
    retrieval = config.get("retrieval") or {}
    hnsw = retrieval.get("hnsw") if isinstance(retrieval, dict) else None
    hnsw = hnsw if isinstance(hnsw, dict) else {}
    try:
        cfg_m = int(hnsw.get("m", 16))
        cfg_ef = int(hnsw.get("ef_construction", 128))
    except (TypeError, ValueError):
        cfg_m, cfg_ef = 16, 128
    row = await conn.fetchrow(HNSW_RELOPTIONS_SQL, HNSW_INDEX_NAME)
    if row is None or row["reloptions"] is None:
        return ResearchCheck(
            name=name,
            status=STATUS_WARN,
            detail=f"{HNSW_INDEX_NAME} not found (pgvector < 0.7 skips the halfvec index)",
            fix="run: corpus-kb setup (applies migration 025)",
            doc=DOC_ADMIN,
        )
    options: dict[str, str] = {}
    for raw in row["reloptions"]:
        text = str(raw)
        if "=" in text:
            key, value = text.split("=", 1)
            options[key.strip()] = value.strip()
    live_m = options.get("m")
    live_ef = options.get("ef_construction")
    if live_m == str(cfg_m) and live_ef == str(cfg_ef):
        return ResearchCheck(
            name=name,
            status=STATUS_OK,
            detail=f"{HNSW_INDEX_NAME} matches config (m={cfg_m}, ef_construction={cfg_ef})",
            doc=DOC_ADMIN,
        )
    return ResearchCheck(
        name=name,
        status=STATUS_WARN,
        detail=(
            f"{HNSW_INDEX_NAME} was built with m={live_m}, ef_construction={live_ef} "
            f"but config says m={cfg_m}, ef_construction={cfg_ef} "
            "(the index options are build-time; rebuild to apply the config)"
        ),
        fix=(
            f"drop and recreate {HNSW_INDEX_NAME} with the configured options "
            "(see migration 025 for the statement)"
        ),
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
    "hnsw_index_check",
    "unknown_env_vars",
]
