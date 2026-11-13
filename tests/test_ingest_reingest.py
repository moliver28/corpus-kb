"""Bulk re-embed coverage (issue #202, ``ingest-reingest``).

Two tiers:

* Unit (fake-conn Postgres): the receipt contract, the dry-run plan,
  resume counting, batching with keyset order, the width refusal, and
  the ledger rows — the same offline pattern test_ingest_reembed_stale
  uses.
* DuckDB E2E (real engine, migrated tmp file): the same path against
  real SQL — vectors land, resume skips, dry-run reads only, a wrong
  width refuses, and the project scope narrows.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

pytest.importorskip("duckdb")

from corpus_kb.errors import ConfigError
from corpus_kb.storage.duckdb_support import duckdb_path_to_conn_string, vector_literal
from corpus_kb.storage.gateway import DuckDbGateway
from corpus_kb.tools import ingest_reingest, ingest_reingest_run

pytestmark = pytest.mark.asyncio

TENANT = "00000000-0000-0000-0000-000000000001"
PROJECT = "44444444-4444-4444-4444-444444444444"
RUN_ID = "11111111-1111-1111-1111-111111111111"
DIM = 4


def _config(**over: object) -> dict[str, object]:
    embedding: dict[str, object] = {
        "provider": "ollama",
        "model": "new-model",
        "dimensions": DIM,
    }
    embedding.update(over)
    return {"embedding": embedding}


class _FakeConn:
    """Scripted asyncpg conn: the markers reingest_embeddings sends answer."""

    def __init__(
        self,
        *,
        columns_present: int = 2,
        width: str = f"halfvec({DIM})",
        docs: int = 0,
        total: int = 0,
        todo: int = 0,
        est_chars: int = 0,
        run_id: str | None = None,
        scan_pages: list[list[dict[str, object]]] | None = None,
    ) -> None:
        self.sql: list[str] = []
        self._columns_present = columns_present
        self._width = width
        self._docs = docs
        self._total = total
        self._todo = todo
        self._est_chars = est_chars
        self._run_id = run_id
        self._scan_pages = list(scan_pages or [])
        self.val_args: tuple[object, ...] = ()
        self.fetch_args: tuple[object, ...] = ()

    async def execute(self, sql: str, *args: object) -> str:
        self.sql.append(sql)
        return "OK"

    async def executemany(self, sql: str, args: list[object]) -> str:
        self.sql.append(sql)
        return "OK"

    async def fetchrow(self, sql: str, *args: object) -> dict[str, object] | None:
        self.sql.append(sql)
        if "atttypmod" in sql:
            return {"typ": self._width, "width": ingest_reingest_width(self._width)}
        return None

    async def fetchval(self, sql: str, *args: object) -> object:
        self.sql.append(sql)
        self.val_args = args
        if "information_schema" in sql:
            return self._columns_present
        if "format_type" in sql:
            return self._width
        if "RETURNING run_id" in sql:
            return self._run_id
        if "count(DISTINCT c.doc_id)" in sql:
            return self._docs
        if "char_length" in sql:
            return self._est_chars
        if "IS DISTINCT FROM" in sql:
            return self._todo
        return self._total

    async def fetch(self, sql: str, *args: object) -> list[dict[str, object]]:
        self.sql.append(sql)
        self.fetch_args = args
        if "PRAGMA" in sql or "duckdb_" in sql:
            return []
        return self._scan_pages.pop(0) if self._scan_pages else []

    def transaction(self) -> _FakeConn:
        return self

    async def __aenter__(self) -> _FakeConn:
        return self

    async def __aexit__(self, *exc: object) -> bool:
        return False

    class _Acquire:
        def __init__(self, conn: _FakeConn) -> None:
            self._conn = conn

        async def __aenter__(self) -> _FakeConn:
            return self._conn

        async def __aexit__(self, *exc: object) -> bool:
            return False

    def acquire(self) -> _Acquire:
        return _FakeConn._Acquire(self)


def ingest_reingest_width(width: str) -> int:
    return int(width.rstrip(")").rsplit("(", 1)[1])


class _FakePool:
    def __init__(self, conn: _FakeConn) -> None:
        self._conn = conn

    def acquire(self) -> _FakeConn._Acquire:
        return self._conn.acquire()


_scan_counter = {"n": 0}


def _scan_row(text: str = "body text", blurb: str | None = "a blurb") -> dict[str, object]:
    _scan_counter["n"] += 1
    return {
        "chunk_id": f"chunk-{_scan_counter['n']:03d}",
        "doc_id": "doc-1",
        "text": text,
        "context_blurb": blurb,
    }


def _ok_embed(calls: list[dict[str, object]], dims: int = DIM):
    async def fake_embed(
        chunks_in: list[Any],
        config: dict[str, object],
        pool: object = None,
        text_override: dict[str, str] | None = None,
        document_text: str | None = None,
        meta: dict[str, object] | None = None,
    ):
        calls.append({"chunks": list(chunks_in), "text_override": text_override})
        for chunk in chunks_in:
            chunk.embedding = [0.5] * dims
        if meta is not None:
            meta["embedding_mode"] = "pooled"
        return False, None

    return fake_embed


async def test_dry_run_reports_plan_without_embed_or_write(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Dry-run is a read-only plan: counts + est_tokens, no embed, no ledger."""
    monkeypatch.delenv("CORPUS_KB_DATABASE_URL", raising=False)
    embed_calls: list[dict[str, object]] = []
    monkeypatch.setattr(ingest_reingest_run, "embed_chunks", _ok_embed(embed_calls))
    conn = _FakeConn(docs=2, total=5, todo=3, est_chars=1000)

    receipt = await ingest_reingest.reingest_embeddings(
        _FakePool(conn), _config(), TENANT, dry_run=True
    )
    assert receipt == {
        "status": "success",
        "dry_run": True,
        "target_model": "new-model",
        "target_dimensions": DIM,
        "documents": 2,
        "chunks": 3,
        "embedded": 0,
        "failed": 0,
        "skipped_current": 2,
        "est_tokens": 250,
    }
    assert not embed_calls
    assert not any("INSERT INTO ingest_runs" in s for s in conn.sql)
    assert not any("UPDATE chunks_vectors" in s for s in conn.sql)
    assert not any("ORDER BY c.doc_id" in s for s in conn.sql)


async def test_run_reembeds_and_reports(monkeypatch: pytest.MonkeyPatch) -> None:
    """A run embeds the scanned chunks and closes the ledger row completed."""
    monkeypatch.delenv("CORPUS_KB_DATABASE_URL", raising=False)
    embed_calls: list[dict[str, object]] = []
    monkeypatch.setattr(ingest_reingest_run, "embed_chunks", _ok_embed(embed_calls))
    rows = [_scan_row(), _scan_row()]
    conn = _FakeConn(docs=1, total=2, todo=2, run_id=RUN_ID, scan_pages=[rows])

    receipt = await ingest_reingest.reingest_embeddings(_FakePool(conn), _config(), TENANT)
    assert receipt["status"] == "success"
    assert receipt["chunks"] == 2
    assert receipt["embedded"] == 2
    assert receipt["failed"] == 0
    assert receipt["run_id"] == RUN_ID
    # The embed input is the rebuilt blurb+text form, per chunk.
    assert embed_calls[0]["text_override"] == {
        "chunk-001": "a blurb\n\nbody text",
        "chunk-002": "a blurb\n\nbody text",
    }
    assert any("INSERT INTO ingest_runs" in s for s in conn.sql)
    # The write is the pipeline's ON CONFLICT upsert, not a repair UPDATE.
    assert any("chunks_vectors" in s and "ON CONFLICT" in s for s in conn.sql)
    finish = next(s for s in conn.sql if "finish_hash" in s)
    assert "status = $2" in finish
    # The per-batch progress updates ran against the open row.
    assert any("stages = $2::jsonb" in s for s in conn.sql)


async def test_zero_todo_run_is_a_noop(monkeypatch: pytest.MonkeyPatch) -> None:
    """A corpus already at the target answers success with zero work."""
    monkeypatch.delenv("CORPUS_KB_DATABASE_URL", raising=False)
    embed_calls: list[dict[str, object]] = []
    monkeypatch.setattr(ingest_reingest_run, "embed_chunks", _ok_embed(embed_calls))
    conn = _FakeConn(docs=1, total=7, todo=0)

    receipt = await ingest_reingest.reingest_embeddings(_FakePool(conn), _config(), TENANT)
    assert receipt["chunks"] == 0
    assert receipt["skipped_current"] == 7
    assert not embed_calls
    assert not any("INSERT INTO ingest_runs" in s for s in conn.sql)
    assert not any("ORDER BY c.doc_id" in s for s in conn.sql)


async def test_failed_zero_vectors_counted(monkeypatch: pytest.MonkeyPatch) -> None:
    """All-zero vectors are failures: counted, error-recorded, run failed."""
    monkeypatch.delenv("CORPUS_KB_DATABASE_URL", raising=False)

    async def bad_embed(
        chunks_in: list[Any],
        config: dict[str, object],
        pool: object = None,
        text_override: dict[str, str] | None = None,
        document_text: str | None = None,
        meta: dict[str, object] | None = None,
    ):
        chunks_in[0].embedding = [0.5] * DIM
        chunks_in[1].embedding = [0.0] * DIM
        return True, "Connection failed: stub embedder returned zero vectors"

    monkeypatch.setattr(ingest_reingest_run, "embed_chunks", bad_embed)
    rows = [_scan_row(), _scan_row()]
    conn = _FakeConn(docs=1, total=2, todo=2, run_id=RUN_ID, scan_pages=[rows])

    receipt = await ingest_reingest.reingest_embeddings(_FakePool(conn), _config(), TENANT)
    assert receipt["embedded"] == 1
    assert receipt["failed"] == 1
    assert receipt["errors"] == ["Connection failed: stub embedder returned zero vectors"]
    assert "warning" not in receipt
    # The pipeline write path keeps both rows: the zero vector lands flagged.
    assert any("chunks_vectors" in s and "ON CONFLICT" in s for s in conn.sql)


async def test_width_mismatch_refuses_before_ledger(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A configured width the column cannot hold is the typed ConfigError."""
    monkeypatch.delenv("CORPUS_KB_DATABASE_URL", raising=False)
    conn = _FakeConn(docs=1, total=3, todo=3, width=f"halfvec({DIM})")

    with pytest.raises(ConfigError) as exc:
        await ingest_reingest.reingest_embeddings(_FakePool(conn), _config(dimensions=8), TENANT)
    assert str(DIM) in str(exc.value)
    assert not any("INSERT INTO ingest_runs" in s for s in conn.sql)


async def test_unavailable_when_columns_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    """A pre-migration store answers the honest unavailable receipt."""
    monkeypatch.delenv("CORPUS_KB_DATABASE_URL", raising=False)
    conn = _FakeConn(columns_present=0)

    receipt = await ingest_reingest.reingest_embeddings(_FakePool(conn), _config(), TENANT)
    assert receipt["status"] == "unavailable"
    assert receipt["reason"]
    assert receipt["remedy"]


async def test_batching_pages_in_keyset_order(monkeypatch: pytest.MonkeyPatch) -> None:
    """Batches page deterministically until the scan runs dry."""
    monkeypatch.delenv("CORPUS_KB_DATABASE_URL", raising=False)
    embed_calls: list[dict[str, object]] = []
    monkeypatch.setattr(ingest_reingest_run, "embed_chunks", _ok_embed(embed_calls))
    page1 = [_scan_row(), _scan_row()]
    page2 = [_scan_row()]
    conn = _FakeConn(docs=1, total=3, todo=3, run_id=RUN_ID, scan_pages=[page1, page2])

    receipt = await ingest_reingest.reingest_embeddings(
        _FakePool(conn), _config(), TENANT, batch_size=2
    )
    assert receipt["embedded"] == 3
    scans = [s for s in conn.sql if "ORDER BY c.doc_id" in s]
    assert len(scans) == 3  # two pages + the closing empty read


async def test_surface_validation_rejects_bad_args(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Bad project id / batch size are structured errors, never crashes."""
    monkeypatch.delenv("CORPUS_KB_DATABASE_URL", raising=False)
    conn = _FakeConn()
    bad_project = await ingest_reingest.ingest_reingest(
        _FakePool(conn), _config(), TENANT, project_id="not-a-uuid"
    )
    assert bad_project == {"status": "error", "message": "project_id is not a UUID: not-a-uuid"}
    small_batch = await ingest_reingest.ingest_reingest(
        _FakePool(conn), _config(), TENANT, batch_size=0
    )
    assert small_batch == {"status": "error", "message": "batch_size must be >= 1, got 0"}


async def test_project_scope_binds_the_project(monkeypatch: pytest.MonkeyPatch) -> None:
    """A project-scoped run narrows the plan to that project's documents."""
    monkeypatch.delenv("CORPUS_KB_DATABASE_URL", raising=False)
    embed_calls: list[dict[str, object]] = []
    monkeypatch.setattr(ingest_reingest_run, "embed_chunks", _ok_embed(embed_calls))
    rows = [_scan_row()]
    conn = _FakeConn(docs=1, total=1, todo=1, run_id=RUN_ID, scan_pages=[rows])

    receipt = await ingest_reingest.reingest_embeddings(
        _FakePool(conn), _config(), TENANT, project_id=PROJECT
    )
    assert receipt["documents"] == 1
    assert PROJECT in conn.fetch_args  # the scan narrowed to the project
    scan = next(s for s in conn.sql if "ORDER BY c.doc_id" in s)
    assert "d.project_id = $2" in scan


# ---------------------------------------------------------------------------
# DuckDB E2E: the real second engine, migrated tmp file, real SQL
# ---------------------------------------------------------------------------

STUB_DIMS = 2560


def _stub_vector(text: str) -> list[float]:
    import hashlib

    seed = int(hashlib.sha256(text.encode("utf-8")).hexdigest()[:8], 16)
    return [((seed + i) % 997) / 997.0 + 0.001 for i in range(STUB_DIMS)]


def _duckdb_config(**over: object) -> dict[str, object]:
    base = {"model": "new-model", "dimensions": STUB_DIMS}
    base.update(over)
    return _config(**base)


async def _seed(gateway: DuckDbGateway, doc_id: str, chunks: list[str], project: str | None = None):
    async with gateway.transaction(TENANT) as tx:
        await tx.execute(
            "INSERT INTO documents (doc_id, tenant_id, source, source_type, chunk_count) "
            "VALUES ($1, $2, $3, 'text', $4)",
            doc_id,
            TENANT,
            f"{doc_id}.txt",
            len(chunks),
        )
        if project:
            await tx.execute(
                "UPDATE documents SET project_id = $2 WHERE doc_id = $1", doc_id, project
            )
        for index, text in enumerate(chunks):
            chunk_id = f"{doc_id}-c{index}"
            await tx.execute(
                "INSERT INTO chunks (chunk_id, tenant_id, doc_id, chunk_index, text) "
                "VALUES ($1, $2, $3, $4, $5)",
                chunk_id,
                TENANT,
                doc_id,
                index,
                text,
            )
            await tx.execute(
                "INSERT INTO chunks_vectors (chunk_id, tenant_id, embedding, "
                "embedding_model, dimensions) VALUES ($1, $2, $3::FLOAT[2560], 'old-model', 2560)",
                chunk_id,
                TENANT,
                vector_literal(_stub_vector(text)),
            )


def _stub_embed(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, object]]:
    calls: list[dict[str, object]] = []

    async def fake_embed(
        chunks_in: list[Any],
        config: dict[str, object],
        pool: object = None,
        text_override: dict[str, str] | None = None,
        document_text: str | None = None,
        meta: dict[str, object] | None = None,
    ):
        calls.append({"chunks": list(chunks_in), "text_override": dict(text_override or {})})
        for chunk in chunks_in:
            source = (text_override or {}).get(str(chunk.chunk_id), chunk.text)
            chunk.embedding = _stub_vector(source)
        return False, None

    monkeypatch.setattr(ingest_reingest_run, "embed_chunks", fake_embed)
    return calls


@pytest.fixture
async def duckdb_reingest_gateway(tmp_path: Path):
    path = tmp_path / "reingest-e2e.duckdb"
    from corpus_kb._setup.migrate import run_migrations

    await run_migrations(duckdb_path_to_conn_string(str(path)))
    gateway = await DuckDbGateway.connect(str(path))
    try:
        yield gateway
    finally:
        await gateway.close()
        for suffix in ("", ".wal"):
            Path(f"{path}{suffix}").unlink(missing_ok=True)


async def test_duckdb_reingest_updates_vectors(
    duckdb_reingest_gateway: DuckDbGateway, monkeypatch: pytest.MonkeyPatch
) -> None:
    gw = duckdb_reingest_gateway
    await _seed(gw, "doc-a", ["alpha body", "beta body"])
    calls = _stub_embed(monkeypatch)

    receipt = await ingest_reingest.reingest_embeddings(gw, _duckdb_config(), TENANT)
    assert receipt["status"] == "success"
    assert receipt["dry_run"] is False
    assert receipt["documents"] == 1
    assert receipt["chunks"] == 2
    assert receipt["embedded"] == 2
    assert receipt["failed"] == 0
    assert receipt["skipped_current"] == 0
    assert receipt["target_model"] == "new-model"
    assert receipt["run_id"]
    assert calls[0]["text_override"] == {
        "doc-a-c0": "alpha body",
        "doc-a-c1": "beta body",
    }
    async with gw.transaction(TENANT) as tx:
        rows = await tx.fetch(
            "SELECT embedding_model, dimensions, degraded FROM chunks_vectors "
            "WHERE tenant_id = $1 ORDER BY chunk_id",
            TENANT,
        )
    assert [dict(r) for r in rows] == [
        {"embedding_model": "new-model", "dimensions": STUB_DIMS, "degraded": False}
    ] * 2


async def test_duckdb_resume_skips_current(
    duckdb_reingest_gateway: DuckDbGateway, monkeypatch: pytest.MonkeyPatch
) -> None:
    gw = duckdb_reingest_gateway
    await _seed(gw, "doc-a", ["alpha body", "beta body"])
    _stub_embed(monkeypatch)
    first = await ingest_reingest.reingest_embeddings(gw, _duckdb_config(), TENANT)
    assert first["embedded"] == 2
    second = await ingest_reingest.reingest_embeddings(gw, _duckdb_config(), TENANT)
    assert second["chunks"] == 0
    assert second["embedded"] == 0
    assert second["skipped_current"] == 2


async def test_duckdb_dry_run_reads_only(
    duckdb_reingest_gateway: DuckDbGateway, monkeypatch: pytest.MonkeyPatch
) -> None:
    gw = duckdb_reingest_gateway
    await _seed(gw, "doc-a", ["alpha body", "beta body"])
    calls = _stub_embed(monkeypatch)

    receipt = await ingest_reingest.reingest_embeddings(gw, _duckdb_config(), TENANT, dry_run=True)
    assert receipt["dry_run"] is True
    assert receipt["chunks"] == 2
    assert receipt["est_tokens"] > 0
    assert not calls
    async with gw.transaction(TENANT) as tx:
        models = await tx.fetchval(
            "SELECT count(*) FROM chunks_vectors WHERE tenant_id = $1"
            " AND embedding_model = 'old-model'",
            TENANT,
        )
    assert models == 2


async def test_duckdb_width_mismatch_refuses(
    duckdb_reingest_gateway: DuckDbGateway, monkeypatch: pytest.MonkeyPatch
) -> None:
    gw = duckdb_reingest_gateway
    await _seed(gw, "doc-a", ["alpha body"])
    _stub_embed(monkeypatch)

    with pytest.raises(ConfigError):
        await ingest_reingest.reingest_embeddings(gw, _duckdb_config(dimensions=100), TENANT)


async def test_duckdb_project_scope_narrows(
    duckdb_reingest_gateway: DuckDbGateway, monkeypatch: pytest.MonkeyPatch
) -> None:
    gw = duckdb_reingest_gateway
    await _seed(gw, "doc-a", ["alpha body"])
    await _seed(gw, "doc-b", ["gamma body", "delta body"], project=PROJECT)
    calls = _stub_embed(monkeypatch)

    receipt = await ingest_reingest.reingest_embeddings(
        gw, _duckdb_config(), TENANT, project_id=PROJECT
    )
    assert receipt["documents"] == 1
    assert receipt["chunks"] == 2
    assert receipt["embedded"] == 2
    assert {c.chunk_id for c in calls[0]["chunks"]} == {"doc-b-c0", "doc-b-c1"}
    async with gw.transaction(TENANT) as tx:
        old = await tx.fetchval(
            "SELECT count(*) FROM chunks_vectors WHERE tenant_id = $1"
            " AND embedding_model = 'old-model'",
            TENANT,
        )
    assert old == 1  # only doc-a kept the old space
