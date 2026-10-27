"""The production calibration engine: scratch tenants on the configured Postgres.

The plan drafted throwaway databases; throwaway TENANTS give the same
isolation with zero DDL. Every candidate ingests under its own synthetic
tenant UUID (a dedicated uuid5 namespace, so a collision with a real
tenant is impossible), and cleanup is a set of parameterized DELETEs that
ride the documents cascade. The tenant name derives from the golden
digest, so the same labels always sweep the same tenant and ``force``
re-ingesting replaces the old sample cleanly.

Sample files ingest through the ONE research ingest operation
(``research_ingest_transcript``); ranking reuses the eval runner's fused
arms so the sweep measures exactly the retrieval path research users hit.
The sample files must be session transcripts: the sweep ranks the research
units they produce. A zero-vector probe answers the degraded flag, so a
downed embedder refuses to score instead of producing fake numbers.

Cleanup covers the synthetic tenant's aux tables and ``ingested_files``
rows plus the documents root, whose cascade removes chunks, units, and the
other children; event-store rows for the synthetic tenant remain as
operational residue.
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping
from pathlib import Path
from typing import cast
from uuid import UUID, uuid5

import asyncpg

from corpus_kb.errors import ValidationError
from corpus_kb.eval.labels import ChunkEntry, LabelIndex
from corpus_kb.eval.scoring import IngestedDoc

__all__ = ["ScratchTenantEngine", "tenant_for"]

# Cleanup order: children that do NOT hang off documents first, then the
# document root (its cascade removes chunks, units, exchanges, speakers,
# statements, and shadow vectors). ``ingested_files`` is INGEST-OWNED and
# does not cascade from documents, so it is deleted explicitly.
_AUX_TABLES = (
    "research_noise_queue",
    "research_proposed_codes",
    "research_observations",
    "research_keyword_hits",
    "research_exact_cache",
    "research_projects",
    "ingested_files",
)


def tenant_for(profile_name: str, golden_sha256: str, candidate: str) -> UUID:
    """The synthetic tenant for one candidate: stable per labels, never real."""
    return uuid5(
        uuid.uuid5(uuid.NAMESPACE_URL, "corpus-kb:calibration-sweep"),
        f"{profile_name}:{golden_sha256}:{candidate}",
    )


def _connection_string(cfg: Mapping[str, object]) -> str:
    """The user's Postgres DSN from config (env overrides already applied)."""
    raw = cfg.get("database")
    database: Mapping[str, object] = raw if isinstance(raw, Mapping) else {}
    dsn = database.get("connection_string")
    if isinstance(dsn, str) and dsn:
        return dsn
    from corpus_kb.config import load_config

    loaded = load_config()
    loaded_database = loaded.get("database")
    loaded_dsn = (
        loaded_database.get("connection_string") if isinstance(loaded_database, Mapping) else None
    )
    if isinstance(loaded_dsn, str) and loaded_dsn:
        return loaded_dsn
    raise ValidationError(
        "No Postgres connection string found for the calibration sweep.",
        remediation="Set database.connection_string in config.yaml or CORPUS_KB_DATABASE_URL.",
    )


class ScratchTenantEngine:
    """One candidate's scratch tenant: probe, ingest, index, rank, clean."""

    def __init__(self, cfg: Mapping[str, object], tenant_id: UUID, min_overlap: float) -> None:
        self._cfg: dict[str, object] = dict(cfg)
        self._tenant = tenant_id
        self._min_overlap = min_overlap
        self._pool: asyncpg.Pool | None = None
        self._degraded = False

    async def open(self) -> None:
        from corpus_kb.rag import create_embedder
        from corpus_kb.rag.embedder import aembed_batch

        self._pool = await asyncpg.create_pool(_connection_string(self._cfg))
        vectors = await aembed_batch(create_embedder(self._cfg, self._pool), ["calibration probe"])
        self._degraded = not vectors or all(not any(vector) for vector in vectors)

    def _pool_or_raise(self) -> asyncpg.Pool:
        if self._pool is None:
            raise RuntimeError("sweep engine used before open()")
        return self._pool

    async def ingest(self, path: Path, strategy: str) -> IngestedDoc:
        """Ingest through the research handler (the ONE transcript operation).

        The handler is built DIRECTLY (not via ``research_ingest_transcript``)
        because the sweep needs the event store pointed at its own DSN, and
        the public tool seam intentionally does not carry one.
        """
        from corpus_kb.handlers.research_handler import ResearchHandler
        from corpus_kb.projections.catchup import advance_all_projections

        handler = ResearchHandler(self._pool_or_raise(), dsn=_connection_string(self._cfg))
        result = await handler.ingest_transcript(
            self._tenant,
            str(path),
            force=True,
            validate_mode="off",
        )
        await advance_all_projections(self._pool_or_raise(), self._tenant)
        return IngestedDoc(
            doc_id=str(result.get("doc_id", "")),
            degraded=self._degraded,
        )

    async def label_index(self) -> LabelIndex:
        from corpus_kb.research.retrieval import _child_uuid
        from corpus_kb.storage.tenant_conn import tenant_connection

        async with tenant_connection(self._pool_or_raise(), str(self._tenant)) as conn:
            rows = await conn.fetch(
                "SELECT u.unit_id, u.start_char, u.end_char, d.source"
                " FROM research_units u JOIN documents d"
                " ON d.doc_id = u.doc_id AND d.tenant_id = u.tenant_id"
                " WHERE u.tenant_id = $1"
                " AND u.start_char IS NOT NULL AND u.end_char IS NOT NULL",
                self._tenant,
            )
        entries = tuple(
            ChunkEntry(
                chunk_id=_child_uuid(f"unit:{int(row['unit_id'])}"),
                doc=str(row["source"]),
                start=int(row["start_char"]),
                end=int(row["end_char"]),
                unit_ids=(int(row["unit_id"]),),
            )
            for row in rows
        )
        return LabelIndex(entries=entries, min_overlap=self._min_overlap)

    async def rank(self, query: str, k: int) -> list[str]:
        from corpus_kb.eval.runner import NIL_PROJECT_ID, _ranked_chunks
        from corpus_kb.projections.research._embed import ResearchEmbedder
        from corpus_kb.rag import create_embedder
        from corpus_kb.storage.tenant_conn import tenant_connection

        pool = self._pool_or_raise()
        embedder = ResearchEmbedder(pool, create_embedder(self._cfg, pool))
        async with tenant_connection(pool, str(self._tenant)) as conn:
            return await _ranked_chunks(
                conn, embedder, query, str(self._tenant), NIL_PROJECT_ID, k * 2, 60
            )

    async def cleanup(self) -> None:
        """Delete this tenant's rows; the documents cascade takes the rest."""
        if self._pool is None:
            return
        from corpus_kb.storage.tenant_conn import tenant_connection

        # The engine's pool is always asyncpg, so the yielded connection is
        # the asyncpg one; the SqlGateway branch of the helper never occurs.
        async with tenant_connection(self._pool, str(self._tenant)) as conn:
            pg_conn = cast("asyncpg.Connection", conn)
            async with pg_conn.transaction():
                for table in _AUX_TABLES:
                    await pg_conn.execute(f"DELETE FROM {table} WHERE tenant_id = $1", self._tenant)
                await pg_conn.execute("DELETE FROM documents WHERE tenant_id = $1", self._tenant)

    async def close(self) -> None:
        if self._pool is not None:
            await self._pool.close()
            self._pool = None
