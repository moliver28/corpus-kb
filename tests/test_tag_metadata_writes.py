"""The Phase-2 Wave 2 tag/metadata write ops on BOTH engines (D2 parity).

One flow — create tag, apply, untag (missing link = typed error), set
metadata, read back ordered — runs against the DuckDB file gateway AND
the Postgres pool. Every write rides ``tenant_connection`` (the same
scope the handler uses), so the Postgres leg sits inside the RLS tenant
config and the DuckDB leg inside the gateway's transaction scope.
Postgres skips cleanly when the database is not reachable (the conftest
pg_pool contract), so CI's no-Postgres legs still prove the DuckDB arm.

Tag and key names carry a run-unique suffix: the Postgres test database
keeps its rows between runs, so every run writes fresh names and reads
back only its own (deterministic order, rule 9).
"""

from __future__ import annotations

import json
from pathlib import Path
from uuid import UUID, uuid4

import pytest

from corpus_kb._setup.migrate import run_duckdb_migrations
from corpus_kb.errors import NotFoundError, ValidationError
from corpus_kb.handlers.tag_handler import TagHandler
from corpus_kb.storage.duckdb_support import duckdb_path_to_conn_string
from corpus_kb.storage.gateway import is_gateway
from corpus_kb.storage.tenant_conn import tenant_connection

TENANT = UUID("00000000-0000-0000-0000-000000000001")


@pytest.fixture
async def duckdb_tag_store(tmp_path: Path):
    from corpus_kb.storage.gateway import DuckDbGateway

    path = tmp_path / "tags.duckdb"
    run_duckdb_migrations(duckdb_path_to_conn_string(str(path)))
    gateway = await DuckDbGateway.connect(duckdb_path_to_conn_string(str(path)))
    try:
        yield TagHandler(gateway)
    finally:
        await gateway.close()


@pytest.fixture
async def postgres_tag_store(pg_pool):  # pg_pool skips when Postgres is down
    yield TagHandler(pg_pool)


@pytest.fixture(params=["duckdb", "postgres"])
def store(request: pytest.FixtureRequest) -> TagHandler:
    return request.getfixturevalue(f"{request.param}_tag_store")


async def _insert_document(store: TagHandler, doc_id: UUID, source: str) -> None:
    """One documents row so the tag link's FK holds on both engines."""
    if is_gateway(store._pool):
        sql = "INSERT INTO documents (doc_id, tenant_id, source, source_type) VALUES (?, ?, ?, ?)"
    else:
        sql = (
            "INSERT INTO documents (doc_id, tenant_id, source, source_type) VALUES ($1, $2, $3, $4)"
        )
    async with tenant_connection(store._pool, TENANT) as conn:
        await conn.execute(sql, str(doc_id), str(TENANT), source, "markdown")


async def test_create_apply_remove_and_metadata_on_both_engines(store: TagHandler) -> None:
    stem = uuid4().hex[:8]
    doc_id = uuid4()
    await _insert_document(store, doc_id, f"wave2-{stem}.md")

    # 1 CREATE + dedupe: one uniform envelope, the repeat names the state.
    first = await store.handle_add_tag(TENANT, f"w2-{stem}", color="#112233")
    assert first["status"] == "success"
    assert first["name"] == f"w2-{stem}"
    assert UUID(first["tag_id"])
    duplicate = await store.handle_add_tag(TENANT, f"w2-{stem}", color="#ffffff")
    assert duplicate == {"status": "already_exists", "name": f"w2-{stem}"}

    # 2 APPLY: the link answers success; a missing tag is created first.
    tagged = await store.handle_tag_document(TENANT, doc_id, f"w2-{stem}-link")
    assert tagged == {"status": "success", "doc_id": str(doc_id), "tag": f"w2-{stem}-link"}
    again = await store.handle_tag_document(TENANT, doc_id, f"w2-{stem}-link")
    assert again["status"] == "success"  # idempotent relink

    # 3 READ BACK ORDERED: the read answers in name order — the
    # determinism tiebreak (rule 9) on the gateway arm.
    await store.handle_tag_document(TENANT, doc_id, f"z-{stem}")
    await store.handle_tag_document(TENANT, doc_id, f"a-{stem}")
    rows = await store.handle_get_document_tags(TENANT, doc_id)
    assert [r["name"] for r in rows] == sorted(r["name"] for r in rows)
    assert {f"w2-{stem}-link", f"z-{stem}", f"a-{stem}"} <= {r["name"] for r in rows}

    # 4 UNTAG: the link goes away; the repeat is a typed error, not an ok.
    removed = await store.handle_untag_document(TENANT, doc_id, f"w2-{stem}-link")
    assert removed == {"status": "success", "doc_id": str(doc_id), "tag": f"w2-{stem}-link"}
    names_after = {r["name"] for r in await store.handle_get_document_tags(TENANT, doc_id)}
    assert f"w2-{stem}-link" not in names_after
    with pytest.raises(NotFoundError) as missed:
        await store.handle_untag_document(TENANT, doc_id, f"w2-{stem}-link")
    assert f"w2-{stem}-link" in str(missed.value)

    # 5 METADATA: the upsert answers the uniform envelope and the re-set
    # updates the value in place (one row per key per document).
    saved = await store.handle_set_metadata(TENANT, f"k-{stem}", "one", doc_id=doc_id)
    assert saved == {"status": "success", "key": f"k-{stem}", "value": "one"}
    updated = await store.handle_set_metadata(TENANT, f"k-{stem}", "two", doc_id=doc_id)
    assert updated == {"status": "success", "key": f"k-{stem}", "value": "two"}
    meta = await store.handle_get_metadata(TENANT, key=f"k-{stem}", doc_id=doc_id)
    assert [(r["key"], r["value"]) for r in meta] == [(f"k-{stem}", "two")]


async def test_metadata_rows_are_json_safe_on_both_engines(store: TagHandler) -> None:
    """The read-after-write row must cross JSONResponse unchanged.

    The Phase-2 e2e caught this: a non-empty read carried ``doc_id`` as
    a raw asyncpg UUID, the HTTP envelope 400'd with "Object of type UUID
    is not JSON serializable", and the drawer's refresh lost its saved
    banner. The rows are text now, on both engines.
    """
    stem = uuid4().hex[:8]
    doc_id = uuid4()
    await _insert_document(store, doc_id, f"wave3-{stem}.md")
    await store.handle_set_metadata(TENANT, f"k-{stem}", "v", doc_id=doc_id)
    rows = await store.handle_get_metadata(TENANT, doc_id=doc_id)
    assert len(rows) == 1
    assert rows[0]["doc_id"] == str(doc_id)
    json.dumps(rows[0])  # raises if any value is still a raw UUID


async def test_doc_less_set_metadata_is_a_typed_error_on_both_engines(store: TagHandler) -> None:
    """The metadata PK keys every row to one document, so a doc-less
    write can never commit — the handler answers the typed validation
    error, not a raw constraint failure."""
    with pytest.raises(ValidationError) as bad:
        await store.handle_set_metadata(TENANT, f"k-{uuid4().hex[:8]}", "v")
    assert "document" in str(bad.value).lower()


async def test_blank_tag_name_is_a_typed_error_on_both_engines(store: TagHandler) -> None:
    """The schema accepts a blank name, so the handler guards it — the
    create and the apply ops answer the typed error, never a blank row."""
    for name in ("", "   "):
        with pytest.raises(ValidationError):
            await store.handle_add_tag(TENANT, name)
        with pytest.raises(ValidationError):
            await store.handle_tag_document(TENANT, uuid4(), name)
