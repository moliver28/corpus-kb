"""The production calibration engine: ONE scratch tenant per sweep.

The plan drafted throwaway databases; throwaway TENANTS give the same
isolation with zero DDL. One synthetic tenant UUID (a dedicated uuid5
namespace, so a collision with a real tenant is impossible) serves the
WHOLE recipe family: the sample is ingested ONCE, then every candidate
ranks cheaply through its own query-time overrides
(``research.retrieval.ranked_children`` is the rank seam, and
``reranking.enabled`` toggles the reranker argument). Cleanup is a set of
parameterized DELETEs that ride the documents cascade; event-store rows
for the synthetic tenant remain as operational residue.

The engine requires Postgres: the research arms run pgvector + SQL
fusion, which the DuckDB backend does not serve. A ``duckdb`` backend
raises a typed taxonomy error before any connection opens. Sample files
must be session transcripts: the sweep ranks the research units they
produce. A zero-vector probe answers the degraded flag, so a downed
embedder refuses to score instead of producing fake numbers.
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping
from pathlib import Path
from typing import cast
from uuid import UUID, uuid5

import asyncpg

from corpus_kb.errors import DatabaseUnavailableError, ValidationError
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


def tenant_for(profile_name: str, golden_sha256: str) -> UUID:
    """The synthetic tenant for one sweep: stable per labels, never real.

    ONE tenant per (profile, golden) pair — the whole recipe family ranks
    the same ingested corpus, so candidates differ only at query time.
    """
    return uuid5(
        uuid.uuid5(uuid.NAMESPACE_URL, "corpus-kb:calibration-sweep"),
        f"{profile_name}:{golden_sha256}",
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
    """The family's scratch tenant: probe, ingest once, index, rank, clean."""

    def __init__(self, cfg: Mapping[str, object], tenant_id: UUID, min_overlap: float) -> None:
        self._cfg: dict[str, object] = dict(cfg)
        self._tenant = tenant_id
        self._min_overlap = min_overlap
        self._pool: asyncpg.Pool | None = None
        self._degraded = False
        self._ingested: dict[Path, IngestedDoc] = {}

    async def open(self) -> None:
        self._require_postgres()
        from corpus_kb.rag import create_embedder
        from corpus_kb.rag.embedder import aembed_batch

        self._pool = await asyncpg.create_pool(_connection_string(self._cfg))
        vectors = await aembed_batch(create_embedder(self._cfg, self._pool), ["calibration probe"])
        self._degraded = not vectors or all(not any(vector) for vector in vectors)

    def _require_postgres(self) -> None:
        """Refuse the DuckDB backend before any connection opens."""
        raw = self._cfg.get("database")
        database: Mapping[str, object] = raw if isinstance(raw, Mapping) else {}
        if database.get("backend") == "duckdb":
            raise DatabaseUnavailableError(
                "The calibration sweep needs Postgres; the research rank"
                " seam runs pgvector and SQL fusion.",
                remediation=(
                    "Set database.backend to postgres, or point"
                    " database.connection_string at a Postgres database."
                ),
            )

    def _pool_or_raise(self) -> asyncpg.Pool:
        if self._pool is None:
            raise RuntimeError("sweep engine used before open()")
        return self._pool

    async def ingest(self, path: Path, strategy: str) -> IngestedDoc:
        """Ingest through the research handler, ONCE per file.

        The whole family shares the tenant, so a repeated call returns the
        cached result instead of re-ingesting. ``strategy`` is kept for the
        engine protocol and discarded: recipes change rank time, never the
        ingest. The handler is built DIRECTLY (not via
        ``research_ingest_transcript``) because the sweep needs the event
        store pointed at its own DSN, and the public tool seam
        intentionally does not carry one.
        """
        from corpus_kb.handlers.research_handler import ResearchHandler
        from corpus_kb.projections.catchup import advance_all_projections

        cached = self._ingested.get(path)
        if cached is not None:
            return cached
        handler = ResearchHandler(self._pool_or_raise(), dsn=_connection_string(self._cfg))
        result = await handler.ingest_transcript(
            self._tenant,
            str(path),
            force=True,
            validate_mode="off",
        )
        await advance_all_projections(self._pool_or_raise(), self._tenant)
        doc = IngestedDoc(
            doc_id=str(result.get("doc_id", "")),
            degraded=self._degraded,
        )
        self._ingested[path] = doc
        return doc

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

    async def rank(
        self, query: str, k: int, overrides: Mapping[str, object] | None = None
    ) -> list[str]:
        """One rank pass through the research seam, with recipe overrides."""
        from corpus_kb.eval.recipes import apply_overrides
        from corpus_kb.eval.runner import NIL_PROJECT_ID
        from corpus_kb.projections.research._embed import ResearchEmbedder
        from corpus_kb.rag import create_embedder
        from corpus_kb.rag.rerank_settings import load_rerank_settings
        from corpus_kb.rag.reranker import build_reranker
        from corpus_kb.research.retrieval import ResearchQuery, ranked_children
        from corpus_kb.research.retrieval_settings import load_retrieval_settings

        pool = self._pool_or_raise()
        merged = apply_overrides(self._cfg, overrides or {})
        embedder = ResearchEmbedder(pool, create_embedder(merged, pool))
        reranker = build_reranker(merged)
        q = ResearchQuery(
            query=query,
            tenant_id=self._tenant,
            project_id=UUID(NIL_PROJECT_ID),
            k=k,
            include_exchange_children=False,
            rerank=reranker is not None,
        )
        rows = await ranked_children(
            pool,
            embedder,
            q,
            reranker=reranker,
            retrieval_settings=load_retrieval_settings(merged),
        )
        if reranker is not None:
            floor = float(load_rerank_settings(merged).score_floor)
            if floor > 0.0:
                rows = [row for row in rows if float(row["score"]) >= floor]
        return [str(row["chunk_id"]) for row in rows][:k]

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
