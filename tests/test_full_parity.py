"""Full-parity harness: one golden corpus, both storage engines (program §12).

Every leg runs the SAME operations over the SAME fixed golden corpus
(tests/full_parity_corpus.py):

* golden search envelopes (QueryHandler, single-anchor family) — byte-equal
  across engines after the declared index-mode mask;
* the adversarial FTS battery — grounded in the W0 spikes
  (findings/search-fts.md), each query pinned to its declared equivalence;
* zero-vector/degraded handling — excluded and flagged identically; the
  DuckDB-only sentinel ordering is pinned where PG keeps the row unreachable
  (decision log: "zero-vector cosine clamps to 2.0 vs PG NaN-last");
* document listing, corpus digests and graph hops.

Legs: DuckDB is hermetic (a tmp ``.duckdb`` per test carrying the real
``migrations_duckdb`` mirror; no eventsourcing app is opened, so no
``events_sidecar_path`` sidecar is created — teardown deletes the ``.wal``
beside the file, rule 21). Postgres reuses a session throwaway database
(the ``parity_pg_dsn`` fixture below, on the same env-derived settings as
the research harness), scoped by its own fixed tenant; ``requires_postgres``
skips it cleanly without a live server (the ubuntu CI leg carries the
phase-B service container).

Windows hygiene: the DuckDB connection closes before any tmp file could be
removed; the PG pool closes last.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast
from uuid import UUID

import asyncpg
import pytest
from full_parity_corpus import (
    BATTERY,
    CHUNK_IDS,
    CHUNK_SPECS,
    DEGRADED_CHUNK_IDS,
    DEGRADED_TENANT_ID,
    DOC_IDS,
    ENTITY_IDS,
    ENTITY_SPECS,
    GOLDEN_ANCHOR,
    GOLDEN_QUERIES,
    RELATION_IDS,
    RELATION_SPECS,
    TENANT_ID,
    TENANT_UUID,
    ParityEmbedder,
    digest_rows,
    digest_statements,
    golden_band,
    ids_to_keys,
    mask_index_modes,
    payload_bytes,
    result_ids,
    seed_plan,
)

pytest.importorskip("duckdb")

RRF_K = 60  # retrieval default; the leg config does not override it

_DUCK_HANDLER_CONFIG: dict[str, Any] = {
    "embedding": {"provider": "ollama"},
    "search": {"reranker": "none"},
    "reranking": {"enabled": False},
    "retrieval": {
        "exclude_degraded": True,
        "expand_to_parent": False,
        "self_query": {"enabled": False},
    },
}


# --- The shared plan runners ----------------------------------------------------


async def apply_plan(
    leg: DuckLeg | PgLeg, plan: dict[str, list[tuple[str, tuple[object, ...]]]]
) -> None:
    """Run the seed plan: one RLS scope per tenant on Postgres, flat on
    DuckDB. The statement text is identical for both engines."""
    for tenant, statements in plan.items():
        if isinstance(leg, PgLeg):
            async with await leg.tenant_scope(tenant) as conn:
                for sql, params in statements:
                    await conn.execute(sql, *params)
        else:
            for sql, params in statements:
                await leg.execute(sql, *params)


async def leg_digest(leg: DuckLeg | PgLeg) -> dict[str, tuple[int, str]]:
    """Digest the golden tenant: identical statement text, identical
    canonicalization (migrate_digest policy) on both engines."""
    out: dict[str, tuple[int, str]] = {}
    for sql, table, columns in digest_statements():
        rows = await leg.fetch_all(sql, TENANT_ID)
        out[table] = digest_rows(columns, rows)
    return out


# --- Legs ---------------------------------------------------------------------


class DuckLeg:
    """One hermetic DuckDB file: migrated, seeded, one gateway (single writer)."""

    backend = "duckdb"

    def __init__(self, gateway: Any, path: Path) -> None:
        self.gateway = gateway
        self.path = path

    async def execute(self, sql: str, *params: object) -> None:
        await self.gateway.execute(sql, *params)

    async def fetch_all(self, sql: str, *params: object) -> list[tuple[object, ...]]:
        rows = await self.gateway.fetch(sql, *params)
        # Gateway rows are dicts (asyncpg Records on the PG leg iterate
        # values); extract by position so both legs hand back value tuples.
        return [tuple(dict(row).values()) for row in rows]

    async def seed_corpus(self) -> None:
        await apply_plan(self, seed_plan("JSON", "FLOAT[2560]"))

    async def digest(self) -> dict[str, tuple[int, str]]:
        return await leg_digest(self)

    async def close(self) -> None:
        await self.gateway.close()
        # Rule 21: delete only after close (a .wal sidecar may exist).
        for suffix in ("", ".wal"):
            Path(f"{self.path}{suffix}").unlink(missing_ok=True)


class PgLeg:
    """The session research database scoped to the parity tenant."""

    backend = "postgres"

    def __init__(self, pool: asyncpg.Pool) -> None:
        self.pool = pool

    async def _conn(self, tenant: str = TENANT_ID) -> Any:
        from corpus_kb.storage.tenant_conn import tenant_connection

        return tenant_connection(self.pool, tenant)

    async def fetch_all(self, sql: str, *params: object) -> list[tuple[object, ...]]:
        async with await self._conn() as conn:
            rows = await conn.fetch(sql, *params)
        return [tuple(row) for row in rows]

    async def seed_corpus(self) -> None:
        # One RLS scope per tenant: the GUC is transaction-local.
        await apply_plan(self, seed_plan("jsonb", "halfvec"))

    async def tenant_scope(self, tenant: str) -> Any:
        from corpus_kb.storage.tenant_conn import tenant_connection

        return tenant_connection(self.pool, tenant)

    async def digest(self) -> dict[str, tuple[int, str]]:
        return await leg_digest(self)

    async def close(self) -> None:
        await self.pool.close()


Leg = DuckLeg | PgLeg

BACKEND_PARAMS = [
    pytest.param("duckdb", id="duckdb"),
    pytest.param("postgres", id="postgres", marks=pytest.mark.requires_postgres),
]


async def build_duck_leg(tmp_path: Path) -> DuckLeg:
    """Hermetic leg: the full migration mirror on a fresh tmp file."""
    from corpus_kb._setup.migrate import run_duckdb_migrations
    from corpus_kb.storage.duckdb_support import duckdb_path_to_conn_string
    from corpus_kb.storage.gateway import DuckDbGateway

    path = tmp_path / "full-parity.duckdb"
    path.parent.mkdir(parents=True, exist_ok=True)
    run_duckdb_migrations(duckdb_path_to_conn_string(path))
    gateway = await DuckDbGateway.connect(str(path))
    return DuckLeg(gateway, path)


async def build_pg_leg(dsn: str) -> PgLeg:
    pool = await asyncpg.create_pool(dsn, min_size=1, max_size=2)
    return PgLeg(pool)


def _run_coro_on_worker(coro: Any) -> Any:
    """Await a coroutine on a worker thread's own loop.

    The parity bootstrap must be reachable from BOTH sync and async fixture
    contexts. ``asyncio.run`` and ``run_until_complete`` refuse to nest
    inside the pytest-asyncio test loop; a worker thread has no running-loop
    marker, so the coroutine runs there and the result crosses by value.
    Nothing loop-bound escapes.
    """
    import concurrent.futures

    def _runner() -> Any:
        import asyncio

        return asyncio.run(coro)

    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
        return executor.submit(_runner).result()


@pytest.fixture(scope="session")
def parity_pg_dsn() -> str:
    """Session throwaway Postgres database for the parity legs.

    conftest's ``research_dsn`` cannot be resolved lazily from an async
    fixture (``asyncio.run`` refuses to run inside the test loop), so the
    harness bootstraps its own scratch database on a separate name through
    the same env-derived settings and the shared ``apply_schema``
    bootstrap, via the worker-thread loop above.
    """
    import research_db

    from corpus_kb._setup.schema_apply import apply_schema

    db_name = "corpus_kb_parity_test"
    owner = research_db._PG_USER

    async def _create() -> str:
        admin = await asyncpg.connect(research_db.super_dsn_for("postgres"))
        try:
            await admin.execute(f'DROP DATABASE IF EXISTS "{db_name}" WITH (FORCE)')
            await admin.execute(f'CREATE DATABASE "{db_name}" OWNER {owner}')
        finally:
            await admin.close()
        superconn = await asyncpg.connect(research_db.super_dsn_for(db_name))
        try:
            await superconn.execute("CREATE EXTENSION IF NOT EXISTS vector")
        finally:
            await superconn.close()
        dsn = f"{research_db.BASE_DSN}/{db_name}"
        await apply_schema(dsn)
        return dsn

    async def _drop() -> None:
        admin = await asyncpg.connect(research_db.super_dsn_for("postgres"))
        try:
            await admin.execute(f'DROP DATABASE IF EXISTS "{db_name}" WITH (FORCE)')
        finally:
            await admin.close()

    dsn = _run_coro_on_worker(_create())
    yield dsn
    _run_coro_on_worker(_drop())


@pytest.fixture(params=BACKEND_PARAMS)
async def leg(request: pytest.FixtureRequest, tmp_path: Path) -> Leg:
    if request.param == "duckdb":
        built: Leg = await build_duck_leg(tmp_path)
    else:
        dsn = request.getfixturevalue("parity_pg_dsn")
        built = await build_pg_leg(dsn)
    await built.seed_corpus()
    try:
        yield built
    finally:
        await built.close()


# --- Shared scenario plumbing ---------------------------------------------------


def vector_text(vector: list[float]) -> str:
    """'[v,v,...]' with no spaces: DuckDB FLOAT cast and pgvector halfvec
    input share this shape (W0 spike 5)."""
    from corpus_kb.storage.duckdb_support import vector_literal

    return cast("str", vector_literal(vector))


def leg_handler(pool: object) -> Any:
    from corpus_kb.handlers.query_handler import QueryHandler

    return QueryHandler(
        cast("Any", pool),
        embedder=ParityEmbedder(),
        reranker=None,
        config=dict(_DUCK_HANDLER_CONFIG),
    )


def leg_pool(leg: Leg) -> object:
    if isinstance(leg, DuckLeg):
        return leg.gateway
    return leg.pool


async def search_payloads(
    handler: Any, queries: tuple[str, ...], tenant: str = TENANT_ID
) -> dict[str, dict[str, Any]]:
    from corpus_kb.domain.models import SearchQuery
    from corpus_kb.handlers.query_integrity import envelope_to_payload

    tenant_uuid = UUID(tenant)
    out: dict[str, dict[str, Any]] = {}
    for query in queries:
        envelope = await handler.handle_search_with_meta(
            SearchQuery(query=query, k=10, tenant_id=tenant_uuid)
        )
        out[query] = envelope_to_payload(envelope)
    return out


# --- Section 1: golden search envelopes ----------------------------------------


@pytest.mark.asyncio
async def test_golden_search_leg_matches_the_shared_expectation(leg: Leg) -> None:
    """Single-anchor queries: anchor first, tie band by chunk id, one total
    order on BOTH engines — the golden family has no engine divergence."""
    from corpus_kb.handlers.query_integrity import (
        PG_LEXICAL_INDEX_MODE,
        PG_VECTOR_INDEX_MODE,
    )
    from corpus_kb.storage.duckdb_search import (
        DUCKDB_LEXICAL_INDEX_MODE,
        DUCKDB_VECTOR_INDEX_MODE,
    )

    handler = leg_handler(leg_pool(leg))
    payloads = await search_payloads(handler, GOLDEN_QUERIES)
    expected_modes = (
        (DUCKDB_VECTOR_INDEX_MODE, DUCKDB_LEXICAL_INDEX_MODE)
        if leg.backend == "duckdb"
        else (PG_VECTOR_INDEX_MODE, PG_LEXICAL_INDEX_MODE)
    )
    for query in GOLDEN_QUERIES:
        payload = payloads[query]
        anchor = GOLDEN_ANCHOR[query]
        keys = ids_to_keys(result_ids(payload))
        assert payload["status"] == "success"
        assert keys == (anchor, *golden_band(anchor)), query
        assert (payload["vector_index_mode"], payload["lexical_index_mode"]) == expected_modes
        assert payload["degraded_excluded"] == {"count": 0, "reasons": []}
        # Rank arithmetic: the anchor hits BOTH arms at rank 1 (1/(k+1) each);
        # every band chunk rides the vector arm's zero-similarity tail.
        scores = [float(row["score"]) for row in payload["result"]]
        assert scores == sorted(scores, reverse=True)
        assert abs(scores[0] - 2.0 / (RRF_K + 1)) < 1e-9
        assert abs(scores[1] - 1.0 / (RRF_K + 2)) < 1e-9
    # Determinism (rule 9): a second full pass is byte-identical.
    again = await search_payloads(handler, GOLDEN_QUERIES)
    assert payload_bytes(again) == payload_bytes(payloads)


@pytest.mark.asyncio
@pytest.mark.requires_postgres
async def test_golden_envelopes_byte_parity_across_backends(
    parity_pg_dsn: str, tmp_path: Path
) -> None:
    """THE centerpiece: both legs, same corpus, byte-equal envelopes after
    the declared index-mode mask (exact-scan vs HNSW and bm25 vs gin are the
    two declared divergences, program §1.1)."""
    duck = await build_duck_leg(tmp_path / "duck")
    pg = await build_pg_leg(parity_pg_dsn)
    try:
        await duck.seed_corpus()
        await pg.seed_corpus()
        duck_payloads = await search_payloads(leg_handler(duck.gateway), GOLDEN_QUERIES)
        pg_payloads = await search_payloads(leg_handler(pg.pool), GOLDEN_QUERIES)
        assert list(duck_payloads) == list(pg_payloads)
        for query in GOLDEN_QUERIES:
            assert payload_bytes(mask_index_modes(duck_payloads[query])) == payload_bytes(
                mask_index_modes(pg_payloads[query])
            ), query
    finally:
        await duck.close()
        await pg.close()


# --- Section 3: adversarial FTS battery ------------------------------------------


async def _run_battery(leg: Leg) -> dict[str, dict[str, Any]]:
    handler = leg_handler(leg_pool(leg))
    return await search_payloads(handler, tuple(case.query for case in BATTERY))


def _assert_battery_leg(payloads: dict[str, dict[str, Any]], backend: str) -> None:
    for case in BATTERY:
        payload = payloads[case.query]
        order = case.duckdb_order if backend == "duckdb" else case.pg_order
        assert payload["status"] == case.status, case.query
        assert tuple(ids_to_keys(result_ids(payload))) == order, case.query
        assert payload["degraded_excluded"]["reasons"] == list(case.reasons), case.query


@pytest.mark.asyncio
async def test_fts_battery_leg_is_valid_stable_and_pinned(leg: Leg) -> None:
    """No battery query crashes: typed envelope, pinned order per engine.

    The DuckDB pins are the W0 spike findings; the PG pins are the matching
    plainto_tsquery semantics verified live (probe: 'walls' both-match,
    hyphen conjunction empties, CJK only as the exact full run).
    """
    payloads = await _run_battery(leg)
    assert payload_bytes(await _run_battery(leg)) == payload_bytes(payloads)
    _assert_battery_leg(payloads, leg.backend)


@pytest.mark.asyncio
@pytest.mark.requires_postgres
async def test_fts_battery_declared_equivalence_across_backends(
    parity_pg_dsn: str, tmp_path: Path
) -> None:
    """Per-query declared equivalence (program §1.1 honesty matrix).

    ``full``: byte-equal envelopes (both engines' lexical arms contribute the
    same rank-1 rows). ``set``: same result ids and status; per-engine rank
    order or fused scores differ because the lexical arms disagree —
    DuckDB's bm25 is disjunctive and length-normalized, Postgres'
    plainto_tsquery is conjunctive ('kestrel-like' ANDs two lexemes and
    matches nothing; DuckDB matches the kestrel chunk and adds a lexical
    rank to the same fused winner).
    """
    duck = await build_duck_leg(tmp_path / "duck")
    pg = await build_pg_leg(parity_pg_dsn)
    try:
        await duck.seed_corpus()
        await pg.seed_corpus()
        duck_payloads = await _run_battery(duck)
        pg_payloads = await _run_battery(pg)
        for case in BATTERY:
            duck_payload, pg_payload = duck_payloads[case.query], pg_payloads[case.query]
            if case.tag == "full":
                assert case.duckdb_order == case.pg_order, case.query
                assert payload_bytes(mask_index_modes(duck_payload)) == payload_bytes(
                    mask_index_modes(pg_payload)
                ), case.query
            else:  # tag == "set": same ids and status; rank order or fused
                # scores differ because the lexical arms disagree.
                assert set(result_ids(duck_payload)) == set(result_ids(pg_payload)), case.query
                assert duck_payload["status"] == pg_payload["status"], case.query
    finally:
        await duck.close()
        await pg.close()


# --- Section 4: zero-vector / degraded parity ------------------------------------


@pytest.mark.asyncio
async def test_degraded_zero_vector_chunk_behaves_identically(leg: Leg) -> None:
    """The zero-vector chunk is still lexically searchable on BOTH engines
    while the vector arm abstains for the unknown-term query (the zero-query
    guard) — same results, same honesty envelope."""
    handler = leg_handler(leg_pool(leg))
    payload = (await search_payloads(handler, ("sable",)))["sable"]
    assert payload["status"] == "success"
    assert result_ids(payload) == [CHUNK_IDS["c_text_zero"]]
    assert payload["degraded_excluded"] == {
        "count": 0,
        "reasons": ["zero-query-embedding"],
    }


@pytest.mark.asyncio
async def test_all_degraded_corpus_fires_the_filter_probe_identically(leg: Leg) -> None:
    """On the ALL-degraded tenant a live (non-zero) query empties the vector
    arm through the degraded filter, so the count probe fires: same excluded
    count, same reason, same lexical survivor on both engines."""
    from corpus_kb.domain.models import SearchQuery
    from corpus_kb.handlers.query_integrity import envelope_to_payload

    handler = leg_handler(leg_pool(leg))
    envelope = await handler.handle_search_with_meta(
        SearchQuery(query="kestrel", k=10, tenant_id=UUID(DEGRADED_TENANT_ID))
    )
    payload = envelope_to_payload(envelope)
    assert payload["status"] == "success"
    assert result_ids(payload) == [DEGRADED_CHUNK_IDS[0]]
    assert payload["degraded_excluded"]["count"] == len(DEGRADED_CHUNK_IDS)
    assert payload["degraded_excluded"]["reasons"] == ["degraded-filter-active"]


@pytest.mark.asyncio
@pytest.mark.requires_postgres
async def test_degraded_envelopes_byte_parity_across_backends(
    parity_pg_dsn: str, tmp_path: Path
) -> None:
    """Same degraded corpora, byte-equal envelopes (index modes masked):
    the excluded zero-vector chunk and the filter-probe honesty record."""
    duck = await build_duck_leg(tmp_path / "duck")
    pg = await build_pg_leg(parity_pg_dsn)
    try:
        await duck.seed_corpus()
        await pg.seed_corpus()
        duck_payloads = await search_payloads(leg_handler(duck.gateway), ("sable",))
        pg_payloads = await search_payloads(leg_handler(pg.pool), ("sable",))
        assert payload_bytes(mask_index_modes(duck_payloads["sable"])) == payload_bytes(
            mask_index_modes(pg_payloads["sable"])
        )
        duck_probe = await search_payloads(
            leg_handler(duck.gateway), ("kestrel",), tenant=DEGRADED_TENANT_ID
        )
        pg_probe = await search_payloads(
            leg_handler(pg.pool), ("kestrel",), tenant=DEGRADED_TENANT_ID
        )
        assert payload_bytes(mask_index_modes(duck_probe["kestrel"])) == payload_bytes(
            mask_index_modes(pg_probe["kestrel"])
        )
    finally:
        await duck.close()
        await pg.close()


@pytest.mark.asyncio
async def test_duckdb_zero_vector_sentinel_sorts_last_when_unfiltered(
    tmp_path: Path,
) -> None:
    """DuckDB-only divergence pin (decision log, W0 spike 3): with the
    degraded filter OFF, the zero-vector row stays reachable at distance 2.0
    and the explicit sentinel sorts it LAST. Postgres cannot run this leg:
    pgvector scores the same row NaN, so the shipped degraded filter is the
    contract that keeps it unreachable there (query_integrity module doc)."""
    from corpus_kb.domain.models import SearchQuery
    from corpus_kb.storage.duckdb_search import DuckDbSearchBackend
    from corpus_kb.storage.search_backend import SearchRequest

    duck = await build_duck_leg(tmp_path)
    try:
        await duck.seed_corpus()
        config = dict(_DUCK_HANDLER_CONFIG)
        config["retrieval"] = dict(_DUCK_HANDLER_CONFIG["retrieval"], exclude_degraded=False)
        backend = DuckDbSearchBackend(embedder=ParityEmbedder(), config=config)
        request = SearchRequest(
            conn=cast("Any", duck.gateway),
            query=SearchQuery(query="marlin", k=10, tenant_id=TENANT_UUID),
            semantic_query="marlin",
            dynamic_predicates=[],
            degraded_pred="",  # filter off: the zero-vector row is reachable
            over_retrieve_n=0,
            reasons=[],
        )
        outcome = await backend.search(request)
        dense_ids = [str(row["chunk_id"]) for row in outcome.vector_rows]
        assert dense_ids[-1] == CHUNK_IDS["c_text_zero"]
        assert CHUNK_IDS["c_text_zero"] in dense_ids
    finally:
        await duck.close()


# --- Section 2b: document listing + corpus digest --------------------------------


@pytest.mark.asyncio
async def test_documents_listing_leg_matches_the_shared_expectation(leg: Leg) -> None:
    """list_documents: one total order (created_at DESC), chunk counts equal
    (stored column on Postgres, computed on the DuckDB gateway arm)."""
    from corpus_kb.domain.models import ListDocumentsQuery

    handler = leg_handler(leg_pool(leg))
    docs = await handler.handle_list_documents(ListDocumentsQuery(tenant_id=TENANT_UUID, limit=10))
    assert [(d.source, d.source_type, d.chunk_count) for d in docs] == [
        ("parity_diary.txt", "text", 3),
        ("parity_notes.md", "markdown", 2),
        ("parity_parser.py", "code", 2),
    ]
    assert [d.doc_id for d in docs] == [
        UUID(DOC_IDS["text"]),
        UUID(DOC_IDS["md"]),
        UUID(DOC_IDS["code"]),
    ]
    # Declared rendering divergence (decision log): the gateway arm renders
    # TIMESTAMPTZ in local time ('2025-12-31 16:00:00-08'), Postgres in UTC.
    # The instant must agree: parse both and compare the UTC date.
    expected_utc_dates = {
        "parity_diary.txt": "2026-01-03",
        "parity_notes.md": "2026-01-02",
        "parity_parser.py": "2026-01-01",
    }
    for doc in docs:
        instant = datetime.fromisoformat(doc.created_at).astimezone(UTC)
        assert instant.isoformat()[:10] == expected_utc_dates[doc.source], doc.created_at


@pytest.mark.asyncio
async def test_corpus_digest_leg_is_stable_and_complete(leg: Leg) -> None:
    """The digest aggregates exist, count the whole golden corpus, and are
    stable across two computations (rule 9)."""
    digests = await leg.digest()
    assert {name: counts for name, (counts, _) in digests.items()} == {
        "documents": 3,
        "chunks": len(CHUNK_SPECS),
        "chunks_vectors": len(CHUNK_SPECS),
        "entities": len(ENTITY_SPECS),
        "relations": len(RELATION_SPECS),
    }
    assert await leg.digest() == digests


@pytest.mark.asyncio
@pytest.mark.requires_postgres
async def test_documents_and_digest_parity_across_backends(
    parity_pg_dsn: str, tmp_path: Path
) -> None:
    """Same corpus: identical document listings and identical per-table
    digests — the migrate-data canonicalization policy (UUIDs as text, one
    UTC zone, jsonb/json through a sorted parse, half-quantized vectors)
    holds across engines on live data."""
    from corpus_kb.domain.models import ListDocumentsQuery

    duck = await build_duck_leg(tmp_path / "duck")
    pg = await build_pg_leg(parity_pg_dsn)
    try:
        await duck.seed_corpus()
        await pg.seed_corpus()
        duck_docs = await leg_handler(duck.gateway).handle_list_documents(
            ListDocumentsQuery(tenant_id=TENANT_UUID, limit=10)
        )
        pg_docs = await leg_handler(pg.pool).handle_list_documents(
            ListDocumentsQuery(tenant_id=TENANT_UUID, limit=10)
        )
        assert [(d.source, d.source_type, d.chunk_count) for d in duck_docs] == [
            (d.source, d.source_type, d.chunk_count) for d in pg_docs
        ]
        assert await duck.digest() == await pg.digest()
    finally:
        await duck.close()
        await pg.close()


# --- Section 2c: graph hops -------------------------------------------------------


def _walk(row: Any) -> dict[str, Any]:
    return {
        "entity_id": str(row["entity_id"]),
        "name": row["name"],
        "entity_type": row["entity_type"],
        "metadata": dict(row["metadata"]),
        "depth": row["depth"],
    }


def _entity_row(entity: Any) -> dict[str, Any]:
    return {
        "entity_id": str(entity.entity_id),
        "name": entity.name,
        "entity_type": entity.entity_type,
        "source_document_id": entity.source_document_id,
        "metadata": dict(entity.metadata),
    }


def _relation_row(relation: Any) -> dict[str, Any]:
    return {
        "relation_id": str(relation.relation_id),
        "source_entity_id": str(relation.source_entity_id),
        "target_entity_id": str(relation.target_entity_id),
        "relation_type": relation.relation_type,
        "weight": relation.weight,
        "metadata": dict(relation.metadata),
    }


async def _graph_scenario(store: Any) -> dict[str, Any]:
    a, b, d = ENTITY_IDS["e_a"], ENTITY_IDS["e_b"], ENTITY_IDS["e_d"]
    return {
        "neighbors_out_h1": [
            _walk(row) for row in await store.neighbors(a, hops=1, direction="out")
        ],
        "neighbors_out_h2_kinds": [
            _walk(row)
            for row in await store.neighbors(a, hops=2, direction="out", relation_types=["kinds"])
        ],
        "neighbors_in_h1": [_walk(row) for row in await store.neighbors(d, hops=1, direction="in")],
        "traverse_d2": [_walk(row) for row in await store.traverse(a, max_depth=2)],
        "search_span": [_entity_row(e) for e in await store.search_entities("Span")],
        "relations_of_b": [_relation_row(r) for r in await store.get_entity_relations(b)],
    }


async def _duck_graph_envelope(tmp_path: Path) -> dict[str, Any]:
    """Hermetic DuckDB leg through DuckDbGraphStore on the mirror schema."""
    import duckdb

    from corpus_kb._setup.migrate import run_duckdb_migrations
    from corpus_kb.storage.duckdb_store import DuckDbGraphStore
    from corpus_kb.storage.duckdb_support import duckdb_path_to_conn_string
    from corpus_kb.utils.models import Entity, Relation

    path = tmp_path / "graph-parity.duckdb"
    run_duckdb_migrations(duckdb_path_to_conn_string(path))
    conn = duckdb.connect(str(path))
    store = DuckDbGraphStore(conn, tenant_id=TENANT_ID)
    try:
        for key, name, entity_type in ENTITY_SPECS:
            await store.add_entity(
                Entity(
                    entity_id=ENTITY_IDS[key],
                    name=name,
                    entity_type=entity_type,
                    source_type="code",
                    metadata={},
                )
            )
        for key, source, target, relation_type, weight in RELATION_SPECS:
            await store.add_relation(
                Relation(
                    relation_id=RELATION_IDS[key],
                    source_entity_id=ENTITY_IDS[source],
                    target_entity_id=ENTITY_IDS[target],
                    relation_type=relation_type,
                    weight=weight,
                )
            )
        return await _graph_scenario(store)
    finally:
        await store.close()
        conn.close()


async def _pg_graph_envelope(parity_pg_dsn: str) -> dict[str, Any]:
    """Live Postgres leg through PostgresGraphStore on the same scenario.

    The parity tenant may already hold the graph fixture rows (seed_corpus
    inserts the same fixed ids), and the relation upsert cannot dedupe a
    NULL-chunk triple — clear the tenant's graph rows first.
    """
    from corpus_kb.storage.graph_store import PostgresGraphStore
    from corpus_kb.storage.tenant_conn import tenant_connection
    from corpus_kb.utils.models import Entity, Relation

    pool = await asyncpg.create_pool(parity_pg_dsn, min_size=1, max_size=2)
    async with tenant_connection(pool, TENANT_ID) as conn:
        await conn.execute("DELETE FROM relations WHERE tenant_id = $1", TENANT_ID)
        await conn.execute("DELETE FROM entities WHERE tenant_id = $1", TENANT_ID)
    store = PostgresGraphStore(pool, tenant_id=TENANT_ID)
    try:
        for key, name, entity_type in ENTITY_SPECS:
            await store.add_entity(
                Entity(
                    entity_id=ENTITY_IDS[key],
                    name=name,
                    entity_type=entity_type,
                    source_type="code",
                    metadata={},
                )
            )
        for key, source, target, relation_type, weight in RELATION_SPECS:
            await store.add_relation(
                Relation(
                    relation_id=RELATION_IDS[key],
                    source_entity_id=ENTITY_IDS[source],
                    target_entity_id=ENTITY_IDS[target],
                    relation_type=relation_type,
                    weight=weight,
                )
            )
        return await _graph_scenario(store)
    finally:
        await store.close()
        await pool.close()


@pytest.mark.asyncio
async def test_graph_hops_duckdb_leg_matches_the_diamond(tmp_path: Path) -> None:
    """Hermetic sanity: the DuckDB store serves the diamond scenario with
    the pinned total orders (depth, name, entity_id)."""
    envelope = await _duck_graph_envelope(tmp_path)
    assert [row["name"] for row in envelope["neighbors_out_h1"]] == ["B-Span", "C-Span"]
    assert [row["name"] for row in envelope["neighbors_out_h2_kinds"]] == ["B-Span", "D-Span"]
    # d sits two hops out: a->b->d (min depth over both diamond paths).
    assert [(row["name"], row["depth"]) for row in envelope["traverse_d2"]] == [
        ("B-Span", 1),
        ("C-Span", 1),
        ("D-Span", 2),
    ]
    assert [row["relation_id"] for row in envelope["relations_of_b"]] == [
        RELATION_IDS["r1"],
        RELATION_IDS["r3"],
    ]


@pytest.mark.asyncio
@pytest.mark.requires_postgres
async def test_graph_hops_parity_across_backends(parity_pg_dsn: str, tmp_path: Path) -> None:
    """Both graph stores run the same diamond; the envelopes are
    byte-identical (shared graph_sql builders make order drift structurally
    impossible — this leg proves it stays that way end to end)."""
    pg_envelope = await _pg_graph_envelope(parity_pg_dsn)
    duck_envelope = await _duck_graph_envelope(tmp_path)
    assert payload_bytes(pg_envelope) == payload_bytes(duck_envelope)
