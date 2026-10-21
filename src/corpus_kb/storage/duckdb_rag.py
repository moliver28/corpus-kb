"""DuckDB RAG backend — the RagBackend arm over the SqlGateway.

Implements the :class:`~corpus_kb.storage.rag_backend.RagBackend` protocol
(reference implementation: ``LlamaIndexPostgresBackend``) on DuckDB. The
backend owns its own ``rag_nodes`` table so it stays decoupled from the
ingest pipeline's ``chunks_vectors`` projection.

DuckDB 1.4 semantics (W0 findings + probes against the pinned wheel):

- ``array_cosine_similarity(FLOAT[k], FLOAT[k])`` exists; ``array_norm``
  does NOT, so the zero guard is ``sqrt(array_inner_product(v, v)) > 0``.
  Zero-vector rows take the sentinel ``-2.0`` and sort LAST — the PG
  NaN-last parity (DuckDB answers -1.0 for undefined cosine).
- Fixed-size array binds go through :func:`vector_literal` (a compact
  varchar) plus an explicit ``::FLOAT[k]`` cast: a Python list bind
  costs seconds per statement on DuckDB 1.4.5.
- The lexical arm is the fts extension's per-table ``match_bm25`` macro.
  The index is static: ``ingest`` rebuilds it after inserts.

Fusion is NOT reimplemented here: callers fetch both arms via
:meth:`DuckDbRagBackend.scored_arms` and fuse them with the exact
``corpus.rrf_fusion`` port at the handlers layer — storage never imports
handlers (program rule 5).
"""

from __future__ import annotations

import inspect
import json
import logging
from typing import Any, Protocol

from corpus_kb.errors import ValidationError
from corpus_kb.storage.gateway import Row, SqlGateway
from corpus_kb.storage.rag_backend import RetrievalResult

logger = logging.getLogger(__name__)

_ZERO_SENTINEL = -2.0


class EmbedsText(Protocol):
    """Minimal embedder surface (sync or async ``embed``)."""

    def embed(self, text: str) -> list[float]: ...


async def _embed_text(embedder: EmbedsText, text: str) -> list[float]:
    if inspect.iscoroutinefunction(embedder.embed):
        embedded = await embedder.embed(text)
        return list(embedded)
    return list(embedder.embed(text))


def vector_literal(vector: list[float]) -> str:
    """Compact array-string for a ``FLOAT[k]`` bind.

    Binding a Python list or numpy array against fixed-size FLOAT[k]
    costs seconds per statement on DuckDB 1.4.5; the same vector as a
    space-free varchar with an explicit SQL cast costs milliseconds.
    (sp-duckdb-search ships the same helper as ``vector_literal()`` in
    its own tree; integration dedupes the two.)
    """
    return "[" + ",".join(repr(float(v)) for v in vector) + "]"


def _vector_sql(table: str, dimensions: int, where: str, limit_param: int) -> str:
    """Cosine score, zero-vector sentinel; ordering zero-flag, score, id
    — the same shape as sp-duckdb-search's vector arm."""
    vec = f"$1::FLOAT[{dimensions}]"
    query_zero = f"sqrt(array_inner_product({vec}, {vec})) = 0"
    stored_zero = "sqrt(array_inner_product(embedding, embedding)) = 0"
    zero = f"({query_zero} OR {stored_zero})"
    return (
        f"SELECT node_id, text, source_id, metadata,"
        f" CASE WHEN {zero} THEN {_ZERO_SENTINEL}"
        f" ELSE array_cosine_similarity(embedding, {vec}) END AS score,"
        f" CASE WHEN {zero} THEN 1 ELSE 0 END AS zero_flag"
        f" FROM {table}{where}"
        f" ORDER BY zero_flag ASC, score DESC, node_id ASC LIMIT ${limit_param}"
    )


def _lexical_sql(table: str, where: str, limit_param: int) -> str:
    """The fts arm: bm25 per node, non-matching (NULL) rows filtered out."""
    macro = f"fts_main_{table}.match_bm25(node_id, $1)"
    return (
        "SELECT node_id, text, source_id, metadata, score FROM ("
        f" SELECT node_id, text, source_id, metadata, {macro} AS score"
        f" FROM {table}{where})"
        " WHERE score IS NOT NULL"
        f" ORDER BY score DESC, node_id ASC LIMIT ${limit_param}"
    )


class DuckDbRagBackend:
    """Async RagBackend on one DuckDB file through the SqlGateway."""

    def __init__(
        self,
        gateway: SqlGateway,
        embedder: EmbedsText,
        *,
        dimensions: int = 768,
        table: str = "rag_nodes",
    ) -> None:
        if dimensions <= 0:
            raise ValidationError("Embedding dimensions must be a positive number.")
        self._gw = gateway
        self._embedder = embedder
        self._dimensions = dimensions
        self._table = table
        self._ready = False

    async def initialize(self) -> None:
        """Create the node table, the source index, and the FTS index."""
        await self._gw.execute(
            f"CREATE TABLE IF NOT EXISTS {self._table} (node_id VARCHAR PRIMARY KEY,"
            " source_id VARCHAR NOT NULL, text VARCHAR NOT NULL,"
            f" embedding FLOAT[{self._dimensions}], metadata JSON DEFAULT '{{}}',"
            " created_at TIMESTAMPTZ NOT NULL DEFAULT now())"
        )
        await self._gw.execute(
            f"CREATE INDEX IF NOT EXISTS idx_rag_source ON {self._table}(source_id)"
        )
        await self._rebuild_fts()
        self._ready = True

    async def ingest(self, source_id: str, chunks: list[dict]) -> None:
        """Index chunks under ``source_id``, replacing its previous rows.

        The FTS index is static in DuckDB, so it is rebuilt after the
        inserts — newly indexed text must be searchable.
        """
        self._require_ready()
        await self.delete_source(source_id)
        for index, chunk in enumerate(chunks):
            embedding = chunk.get("embedding")
            if embedding is None:
                embedding = await _embed_text(self._embedder, str(chunk.get("text", "")))
            node_id = str(chunk.get("node_id") or f"{source_id}_{index}")
            await self._gw.execute(
                f"INSERT INTO {self._table}"
                f" (node_id, source_id, text, embedding, metadata)"
                f" VALUES (?, ?, ?, CAST(? AS FLOAT[{self._dimensions}]), ?)",
                node_id,
                source_id,
                str(chunk.get("text", "")),
                vector_literal(list(embedding)),
                json.dumps(dict(chunk.get("metadata") or {}), default=str),
            )
        await self._rebuild_fts()
        logger.info("Indexed %d rag nodes for source %s", len(chunks), source_id)

    async def retrieve(
        self, query: str, top_k: int, filters: dict | None = None
    ) -> list[RetrievalResult]:
        """Vector arm: cosine similarity, zero vectors sorted last."""
        vec = vector_literal(await _embed_text(self._embedder, query))
        sql = _vector_sql(self._table, self._dimensions, *_where_and_limit(filters))
        rows = await self._fetch(sql, [vec, top_k], filters)
        return [self._to_result(row) for row in rows]

    async def retrieve_lexical(
        self, query: str, top_k: int, filters: dict | None = None
    ) -> list[RetrievalResult]:
        """Lexical arm: the fts match_bm25 macro over the node table."""
        where, limit_param = _where_and_limit(filters)
        sql = _lexical_sql(self._table, where, limit_param)
        rows = await self._fetch(sql, [query, top_k], filters)
        return [self._to_result(row) for row in rows]

    async def scored_arms(
        self, query: str, top_k: int, filters: dict | None = None
    ) -> tuple[list[Row], list[Row]]:
        """Both arms' raw rows, ordered, for caller-side ``rrf_fuse``.

        Rows carry the fusion port's ``chunk_id``/``text``/``source``/
        ``doc_id`` keys (the source id doubles as doc id). ``rrf_fuse``
        parses ids as UUIDs: fusing callers must index with UUID-shaped
        node AND source ids.
        """
        where, limit_param = _where_and_limit(filters)
        fusion_shape = "SELECT node_id AS chunk_id, text, source_id AS source, source_id AS doc_id"
        inner_vec = _vector_sql(self._table, self._dimensions, where, limit_param)
        inner_lex = _lexical_sql(self._table, where, limit_param)
        vec_sql = f"{fusion_shape} FROM ({inner_vec})"
        lex_sql = f"{fusion_shape}, score FROM ({inner_lex})"
        vec = vector_literal(await _embed_text(self._embedder, query))
        vector_rows = await self._fetch(vec_sql, [vec, top_k], filters)
        lexical_rows = await self._fetch(lex_sql, [query, top_k], filters)
        return vector_rows, lexical_rows

    async def delete_source(self, source_id: str) -> None:
        """Remove every indexed chunk for the source id."""
        self._require_ready()
        await self._gw.execute(f"DELETE FROM {self._table} WHERE source_id = ?", source_id)

    async def health(self) -> bool:
        """True when the gateway answers a probe query."""
        try:
            return await self._gw.fetchval("SELECT 1") == 1
        except Exception:
            return False

    # -- internals ---------------------------------------------------------

    async def _rebuild_fts(self) -> None:
        await self._gw.execute("LOAD fts")
        await self._gw.execute(
            f"PRAGMA create_fts_index('{self._table}', 'node_id', 'text', overwrite=1)"
        )

    async def _fetch(self, sql: str, params: list[Any], filters: dict | None) -> list[Row]:
        self._require_ready()
        args = list(params)
        if filters and filters.get("source_id") is not None:
            # The filter binds at $2 and the LIMIT moves to $3 (see
            # _where_and_limit), so the filter goes into slot 1.
            args.insert(1, filters["source_id"])
        return await self._gw.fetch(sql, *args)

    def _require_ready(self) -> None:
        if not self._ready:
            raise RuntimeError("Backend not initialized. Call initialize() first.")

    def _to_result(self, row: Row) -> RetrievalResult:
        metadata = row["metadata"]
        if isinstance(metadata, str):
            metadata = json.loads(metadata)
        score = row["score"]
        return RetrievalResult(
            node_id=str(row["node_id"]),
            source_id=str(row["source_id"]),
            text=str(row["text"]),
            score=float(score) if score is not None else _ZERO_SENTINEL,
            metadata=dict(metadata or {}),
        )


def _where_and_limit(filters: dict | None) -> tuple[str, int]:
    """The WHERE fragment and the LIMIT placeholder index for one arm.

    With a source filter the filter binds at $2, the limit moves to $3.
    """
    if filters and filters.get("source_id") is not None:
        return " WHERE source_id = $2", 3
    return "", 2
