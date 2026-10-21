"""Engine-fit checks for ``corpus-kb doctor`` (storage portability §1.2).

The backend resolution the section probes with, the corpus-size read on
each engine, and the hedged sweet-spot line. The suggestion is INFO, on
purpose: both engines are fully supported (§1.2 — "never a warning").
"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING, Any

from corpus_kb._setup.doctor_research import STATUS_INFO, ResearchCheck

if TYPE_CHECKING:  # function-local at runtime (program rule 9)
    pass

DOC_INSTALL = "docs/INSTALL.md"

#: §1.2 working ceiling, deliberately hedged ("about"): exact-scan kNN
#: suits roughly this many chunks; Postgres + HNSW suits more.
SWEET_SPOT_CHUNKS = 100_000


def resolved_backend(config: dict[str, Any], environ: dict[str, str] | None = None) -> str:
    """The backend the doctor probes: env override first, then config, then postgres."""
    env = os.environ if environ is None else environ
    raw = env.get("CORPUS_KB_DATABASE_BACKEND") or str(
        (config.get("database") or {}).get("backend") or ""
    )
    return raw or "postgres"


def engine_fit_check(backend: str, chunk_count: int | None) -> ResearchCheck:
    """§1.2: the engine in use + the hedged sweet-spot line (never a warning)."""
    ceiling = SWEET_SPOT_CHUNKS // 1000
    if backend == "duckdb":
        detail = "Engine: DuckDB. One process opens the file read-write."
        if chunk_count is not None and chunk_count > SWEET_SPOT_CHUNKS:
            detail += (
                f" The corpus holds about {chunk_count} chunks. Above about "
                f"{ceiling}k, Postgres with HNSW may fit better."
            )
        elif chunk_count is not None:
            detail += f" Exact-scan search stays fast to about {ceiling}k chunks."
        return ResearchCheck(name="engine fit", status=STATUS_INFO, detail=detail, doc=DOC_INSTALL)
    detail = "Engine: Postgres."
    if chunk_count is not None and chunk_count <= SWEET_SPOT_CHUNKS:
        detail += (
            f" A single-user corpus of about {ceiling}k chunks or fewer also "
            "suits DuckDB (corpus-kb setup --backend duckdb)."
        )
    elif chunk_count is not None:
        detail += " This corpus size suits Postgres. The HNSW index keeps search fast."
    return ResearchCheck(name="engine fit", status=STATUS_INFO, detail=detail, doc=DOC_INSTALL)


async def pg_chunk_count(conn_str: str) -> int | None:
    """chunks count under the default-tenant GUC (FORCE RLS zeroes a bare read)."""
    import asyncpg

    from corpus_kb.projections.research._common import DEFAULT_TENANT

    try:
        conn = await asyncpg.connect(conn_str, timeout=3)
    except Exception:
        return None
    try:
        async with conn.transaction():
            await conn.execute(
                "SELECT set_config('app.current_tenant_id', $1, true)", str(DEFAULT_TENANT)
            )
            return int(await conn.fetchval("SELECT count(*) FROM chunks") or 0)
    except Exception:
        return None
    finally:
        await conn.close()


__all__ = ["SWEET_SPOT_CHUNKS", "engine_fit_check", "pg_chunk_count", "resolved_backend"]
