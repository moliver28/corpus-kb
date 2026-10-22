"""Synthetic corpus builder and seeder for the storage benchmark (WP sp-benchmark).

No embedder and no network: chunk ``i`` rides anchor ``i % anchors`` -- a
one-hot direction plus seeded noise, L2-normalized -- so the top-k
neighbors are known by construction. Text is a deterministic word sample;
chunks carry their anchor's query words, so the lexical arm matches a real
subset at every size. The seed write uses each engine's shipped bulk shape:
asyncpg ``executemany`` (pipelined) and gateway multi-row VALUES with the
canonical ``vector_literal`` varchar plus an explicit FLOAT cast (W0
finding: a bound 2560-float ARRAY parameter costs seconds per statement).
The write phase includes app-side vector text formatting on BOTH engines,
so the throughput comparison stays fair.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from uuid import NAMESPACE_URL, uuid5

import asyncpg
import numpy as np

from corpus_kb.storage.duckdb_gateway import DuckDbGateway
from corpus_kb.storage.duckdb_support import vector_literal

#: Rows per vector formatting batch (pins the noise stream; do not change
#: without re-running every size).
SEED_BATCH = 512

#: Words planted per anchor. Not stopwords; lowercase; unique.
QUERY_WORDS: tuple[str, ...] = ("harbor", "signal", "kernel", "atlas", "meadow", "ripple")

_WORD_TEXT = (
    "storage index vector graph chunk query tenant journal ledger beacon lantern "
    "compass anchor current delta ember fjord glacier horizon island jungle kafka "
    "lambda mirror node octagon prism quartz river summit tunnel umbrella vortex "
    "willow xenon yarn zephyr bridge canyon dock estuary format gateway handler "
    "ingest json lexer marshal namespace overlap parser render shard token window"
)


@dataclass(frozen=True)
class BenchCorpus:
    """The deterministic corpus one benchmark size runs against."""

    n_chunks: int
    n_docs: int
    dims: int
    anchors: int
    seed: int
    doc_ids: list[str]
    doc_sources: list[str]
    chunk_ids: list[str]
    chunk_doc: list[int]
    chunk_index: list[int]
    texts: list[str]
    query_vectors_literal: tuple[str, ...]
    query_texts: tuple[str, ...]


def build_corpus(
    n_chunks: int, *, seed: int = 42, dims: int = 2560, anchors: int = 64
) -> BenchCorpus:
    """Build ids, texts and query fixtures; vectors format lazily per batch."""
    n_docs = max(n_chunks // 100, 1)
    ns = f"bench-storage:{seed}:{dims}"
    doc_ids = [str(uuid5(NAMESPACE_URL, f"{ns}:doc:{i}")) for i in range(n_docs)]
    doc_sources = [f"bench://doc-{i:05d}" for i in range(n_docs)]
    chunk_ids = [str(uuid5(NAMESPACE_URL, f"{ns}:chunk:{i}")) for i in range(n_chunks)]
    chunk_doc = [int(min(i // (n_chunks / n_docs), n_docs - 1)) for i in range(n_chunks)]
    chunk_index = [i % 100 for i in range(n_chunks)]

    words = _WORD_TEXT.split()
    rng = np.random.default_rng(seed)
    texts: list[str] = []
    for i in range(n_chunks):
        body = " ".join(rng.choice(words, size=60))
        anchor = i % anchors
        if anchor < len(QUERY_WORDS):
            body = f"{QUERY_WORDS[anchor]} {QUERY_WORDS[(anchor + 1) % len(QUERY_WORDS)]} {body}"
        texts.append(body)

    query_vectors_literal = tuple(_anchor_literal(a, dims) for a in range(3))
    query_texts = tuple(
        f"{QUERY_WORDS[a]} {QUERY_WORDS[(a + 1) % len(QUERY_WORDS)]}" for a in range(3)
    )
    return BenchCorpus(
        n_chunks=n_chunks,
        n_docs=n_docs,
        dims=dims,
        anchors=anchors,
        seed=seed,
        doc_ids=doc_ids,
        doc_sources=doc_sources,
        chunk_ids=chunk_ids,
        chunk_doc=chunk_doc,
        chunk_index=chunk_index,
        texts=texts,
        query_vectors_literal=query_vectors_literal,
        query_texts=query_texts,
    )


def _anchor_literal(anchor: int, dims: int) -> str:
    """The unit one-hot query vector for ``anchor`` in bind-ready literal form."""
    vec = np.zeros(dims, dtype=np.float32)
    vec[anchor] = 1.0
    return vector_literal(vec.tolist())


def vector_batch_strings(start: int, count: int, corpus: BenchCorpus) -> list[str]:
    """Format rows [start, start+count) as engine-ready vector strings.

    Deterministic for the pinned SEED_BATCH: the noise stream seeds on the
    absolute row range, so the same row always gets the same vector. The
    string is the canonical ``vector_literal`` shape (varchar + FLOAT cast
    in the SQL); Postgres parses the same text into halfvec.
    """
    rng = np.random.default_rng([corpus.seed, corpus.dims, start])
    rows = np.zeros((count, corpus.dims), dtype=np.float32)
    anchors = np.arange(start, start + count) % corpus.anchors
    rows[np.arange(count), anchors] = 3.0
    rows += rng.standard_normal((count, corpus.dims)).astype(np.float32) * 0.05
    rows /= np.linalg.norm(rows, axis=1, keepdims=True)
    return [vector_literal(row.tolist()) for row in rows]


_DOC_SQL = (
    "INSERT INTO documents (doc_id, tenant_id, source, source_type, chunk_count) "
    "VALUES ($1, $2, $3, 'text', $4)"
)
_CHUNK_SQL = (
    "INSERT INTO chunks (chunk_id, tenant_id, doc_id, chunk_index, text) "
    "VALUES ($1, $2, $3, $4, $5)"
)
_PG_VEC_SQL = (
    "INSERT INTO chunks_vectors (chunk_id, tenant_id, embedding) VALUES ($1, $2, $3::halfvec)"
)
_DUCK_VEC_HEAD = "INSERT INTO chunks_vectors (chunk_id, tenant_id, embedding) VALUES "
_DUCK_VEC_ROW = "(?, ?, ?::FLOAT[2560])"


def _chunk_rows(corpus: BenchCorpus, tenant_id: str) -> list[tuple[str, str, str, int, str]]:
    return [
        (chunk_id, tenant_id, corpus.doc_ids[d], idx, text)
        for chunk_id, d, idx, text in zip(
            corpus.chunk_ids, corpus.chunk_doc, corpus.chunk_index, corpus.texts, strict=True
        )
    ]


async def seed_postgres(pool: asyncpg.Pool, corpus: BenchCorpus, tenant_id: str) -> float:
    """Write the corpus with asyncpg executemany; return the write seconds.

    One transaction: documents, then chunks, then the vector rows in
    SEED_BATCH groups. HNSW index maintenance sits inside the measured
    write (it is part of Postgres insert cost; DuckDB carries none).
    """
    async with pool.acquire() as conn:
        start = time.perf_counter()
        async with conn.transaction():
            # Tenant GUC first: RLS checks every INSERT (the production
            # transaction scope sets the same GUC).
            await conn.execute("SELECT set_config('app.current_tenant_id', $1, true)", tenant_id)
            await conn.executemany(
                _DOC_SQL,
                [
                    (doc_id, tenant_id, source, corpus.n_chunks // corpus.n_docs)
                    for doc_id, source in zip(corpus.doc_ids, corpus.doc_sources, strict=True)
                ],
            )
            await conn.executemany(_CHUNK_SQL, _chunk_rows(corpus, tenant_id))
            for start_row in range(0, corpus.n_chunks, SEED_BATCH):
                count = min(SEED_BATCH, corpus.n_chunks - start_row)
                vecs = vector_batch_strings(start_row, count, corpus)
                ids = corpus.chunk_ids[start_row : start_row + count]
                await conn.executemany(
                    _PG_VEC_SQL, [(cid, tenant_id, vs) for cid, vs in zip(ids, vecs, strict=True)]
                )
        return time.perf_counter() - start


async def seed_duckdb(gateway: DuckDbGateway, corpus: BenchCorpus, tenant_id: str) -> float:
    """Write the corpus through the gateway; return the write seconds.

    Documents and chunks ride ``write_batch`` (the shipped multi-row VALUES
    path); vector rows ride explicit multi-row VALUES with the varchar plus
    ``?::FLOAT[2560]`` casts. All inside one gateway transaction scope.
    """
    start = time.perf_counter()
    async with gateway.transaction(tenant_id):
        await gateway.write_batch(
            "documents",
            [
                {
                    "doc_id": doc_id,
                    "tenant_id": tenant_id,
                    "source": source,
                    "source_type": "text",
                    "chunk_count": corpus.n_chunks // corpus.n_docs,
                }
                for doc_id, source in zip(corpus.doc_ids, corpus.doc_sources, strict=True)
            ],
        )
        await gateway.write_batch(
            "chunks",
            [
                {
                    "chunk_id": row[0],
                    "tenant_id": tenant_id,
                    "doc_id": row[2],
                    "chunk_index": row[3],
                    "text": row[4],
                }
                for row in _chunk_rows(corpus, tenant_id)
            ],
        )
        for start_row in range(0, corpus.n_chunks, SEED_BATCH):
            count = min(SEED_BATCH, corpus.n_chunks - start_row)
            vecs = vector_batch_strings(start_row, count, corpus)
            ids = corpus.chunk_ids[start_row : start_row + count]
            head = _DUCK_VEC_HEAD + ", ".join([_DUCK_VEC_ROW] * len(vecs))
            params: list[object] = []
            for cid, vs in zip(ids, vecs, strict=True):
                params.extend([cid, tenant_id, vs])
            await gateway.execute(head, *params)
    return time.perf_counter() - start
