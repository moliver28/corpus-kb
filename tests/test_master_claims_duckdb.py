"""Master-membership tables (W4): project_claims, project_activity, master_state.

These tests run the DuckDB migration set against a scratch database and
pin the contracts the claim branch and the active-state module build
on: the three tables exist, and the migration backfill seeds one origin
claim per documents row that carries a project_id.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import duckdb
import pytest

pytest.importorskip("duckdb")

from corpus_kb._setup.migrate import run_migrations
from corpus_kb.storage.duckdb_support import duckdb_path_to_conn_string
from corpus_kb.storage.gateway import DuckDbGateway
from corpus_kb.tools import ingest_common
from corpus_kb.tools.ingest_store import PostgresIngestStore

TENANT = "tenant-1"
P1 = "44444444-4444-4444-4444-444444444444"
P2 = "55555555-5555-5555-5555-555555555555"
DOC_TEXT = "Parity claim seed document body. " * 6
DOC_PATH = "F:/claims/seed.md"
CLEAN_STAGES = '{"errors": []}'


async def _migrated(tmp_path: Path, name: str) -> Path:
    path = tmp_path / name
    await run_migrations(duckdb_path_to_conn_string(path))
    return path


async def test_membership_tables_exist(tmp_path: Path) -> None:
    """A SELECT from each membership table answers zero rows, not an error."""
    path = await _migrated(tmp_path, "membership.duckdb")
    con = duckdb.connect(str(path))
    try:
        for table in ("project_claims", "project_activity", "master_state"):
            rows = con.execute(f"SELECT count(*) FROM {table}").fetchone()
            assert rows == (0,), table
    finally:
        con.close()


async def test_backfill_seeds_origin_claims(tmp_path: Path) -> None:
    """Re-running the migration seeds one claim per origin membership row."""
    path = await _migrated(tmp_path, "backfill.duckdb")
    project_id = "44444444-4444-4444-4444-444444444444"
    doc_id = "00000000-0000-0000-0000-0000000000d1"
    con = duckdb.connect(str(path))
    try:
        con.execute(
            "INSERT INTO documents (doc_id, tenant_id, source, project_id) VALUES (?, ?, ?, ?)",
            [doc_id, TENANT, "F:/seed/x.md", project_id],
        )
        # Re-arm the membership migration: drop only its tracking row.
        con.execute("DELETE FROM schema_migrations WHERE filename = '049_master_membership.sql'")
    finally:
        con.close()
    report = await run_migrations(duckdb_path_to_conn_string(path))
    assert report.applied_now == ["049_master_membership.sql"]
    con = duckdb.connect(str(path))
    try:
        rows = con.execute(
            "SELECT project_id, doc_id, source_project_id FROM project_claims WHERE tenant_id = ?",
            [TENANT],
        ).fetchall()
        assert rows == [(project_id, doc_id, project_id)]
    finally:
        con.close()


# -- W4-4: the hash-first probe and the claim branch (F-06) -----------------


def _seed_doc(con: duckdb.DuckDBPyConnection, doc_id: str, path: str, file_hash: str) -> None:
    con.execute(
        "INSERT INTO documents (doc_id, tenant_id, source, file_hash) VALUES (?, ?, ?, ?)",
        [doc_id, TENANT, path, file_hash],
    )


def _seed_run(
    con: duckdb.DuckDBPyConnection,
    run_id: str,
    path: str,
    file_hash: str,
    *,
    status: str = "completed",
    stages: str = CLEAN_STAGES,
) -> None:
    con.execute(
        "INSERT INTO ingest_runs (run_id, tenant_id, source, start_hash, status, stages)"
        " VALUES (?, ?, ?, ?, ?, ?)",
        [run_id, TENANT, path, file_hash, status, stages],
    )


def _cleanup(path: Path) -> None:
    # DuckDB keeps a .wal sidecar while open; close BEFORE delete.
    for suffix in ("", ".wal"):
        Path(f"{path}{suffix}").unlink(missing_ok=True)


async def test_hash_probe_requires_clean_run(tmp_path: Path) -> None:
    """A hash whose only run is still 'running' never answers unchanged."""
    path = await _migrated(tmp_path, "hash-clean.duckdb")
    con = duckdb.connect(str(path))
    try:
        _seed_doc(con, "00000000-0000-0000-0000-0000000000e1", "F:/a.md", "hash-H")
        _seed_run(con, "run-1", "F:/a.md", "hash-H", status="running")
    finally:
        con.close()
    gateway = await DuckDbGateway.connect(str(path))
    try:
        store = PostgresIngestStore(gateway, TENANT)
        assert await store.unchanged_hash_doc_id("hash-H") is None
    finally:
        await gateway.close()
        _cleanup(path)


async def test_hash_probe_matches_across_paths(tmp_path: Path) -> None:
    """F-06: the probe binds the hash, so path spelling is irrelevant."""
    path = await _migrated(tmp_path, "hash-paths.duckdb")
    con = duckdb.connect(str(path))
    try:
        _seed_doc(con, "00000000-0000-0000-0000-0000000000e2", "F:/a.md", "hash-K")
        _seed_run(con, "run-2", "F:/a.md", "hash-K")
    finally:
        con.close()
    gateway = await DuckDbGateway.connect(str(path))
    try:
        store = PostgresIngestStore(gateway, TENANT)
        assert await store.unchanged_hash_doc_id("hash-K") == (
            "00000000-0000-0000-0000-0000000000e2"
        )
    finally:
        await gateway.close()
        _cleanup(path)


async def test_claim_document_is_idempotent(tmp_path: Path) -> None:
    """The second identical claim writes nothing and answers False."""
    path = await _migrated(tmp_path, "claim-idem.duckdb")
    doc_id = "00000000-0000-0000-0000-0000000000e3"
    con = duckdb.connect(str(path))
    try:
        _seed_doc(con, doc_id, "F:/b.md", "hash-L")
    finally:
        con.close()
    gateway = await DuckDbGateway.connect(str(path))
    try:
        store = PostgresIngestStore(gateway, TENANT)
        assert await store.claim_document(doc_id, P2, P1) is True
        assert await store.claim_document(doc_id, P2, P1) is False
        async with gateway.transaction(TENANT) as tx:
            count = await tx.fetchval("SELECT count(*) FROM project_claims")
        assert count == 1
    finally:
        await gateway.close()
        _cleanup(path)


async def test_claimed_envelope_keys(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The claimed envelope carries exactly the declared keys; X stays one row."""
    from corpus_kb.tools.pipeline.plan import run_pipeline

    _stub_embed_extract(monkeypatch)
    path = await _migrated(tmp_path, "claim-flow.duckdb")
    config: dict[str, Any] = {
        "database": {"backend": "duckdb"},
        "embedding": {"model": "stub-embed", "dimensions": 2560},
        "graph": {"extract_entities": True},
        "ingest": {"contextualize": {"enabled": False}},
    }
    gateway = await DuckDbGateway.connect(str(path))
    try:
        first = await run_pipeline(
            DOC_TEXT, "text", DOC_PATH, config, gateway, TENANT, project_id=P1
        )
        assert first["status"] == "success"
        second = await run_pipeline(
            DOC_TEXT, "text", DOC_PATH, config, gateway, TENANT, project_id=P2
        )
        # run_pipeline decorates the pipeline envelope with the strategy
        # provenance keys (the skipped twin is decorated the same way), so
        # the exact-key pin lives on _claimed_envelope (see the unit test).
        claim_keys = {
            "status",
            "document_id",
            "path",
            "source_type",
            "reason",
            "source_project_id",
            "project_slug",
            "project_scope",
            "errors",
        }
        provenance = {
            "strategy",
            "strategy_basis",
            "profile",
            "detection_confidence",
            "detection_reason",
            "detected_strategy",
            "warnings",
            "textbook",
        }
        assert claim_keys <= set(second) <= claim_keys | provenance
        assert second["status"] == "claimed"
        assert second["source_project_id"] == P1
        async with gateway.transaction(TENANT) as tx:
            count = await tx.fetchval("SELECT count(*) FROM documents WHERE source = $1", DOC_PATH)
        assert count == 1
    finally:
        await gateway.close()
        _cleanup(path)


def test_claimed_envelope_keys_exact() -> None:
    """Review focus 2: the pipeline claimed envelope carries EXACTLY these keys."""
    from corpus_kb.tools.pipeline.envelope import _claimed_envelope

    class _Doc:
        path = "F:/x.md"
        source_type = "text"

    envelope = _claimed_envelope(_Doc(), "doc-1", "proj-9")
    assert set(envelope) == {
        "status",
        "document_id",
        "path",
        "source_type",
        "reason",
        "source_project_id",
        "project_slug",
        "project_scope",
        "errors",
    }
    assert envelope["status"] == "claimed"
    assert envelope["document_id"] == "doc-1"
    assert envelope["source_project_id"] == "proj-9"
    assert envelope["errors"] == []


def _stub_embed_extract(monkeypatch: pytest.MonkeyPatch) -> None:
    """Deterministic embed/extract doubles (no Ollama, no network)."""
    from corpus_kb.extraction import ExtractStats
    from corpus_kb.utils.models import Chunk

    async def fake_embed(
        chunks: list[Chunk],
        config: dict[str, Any],
        pool: Any = None,
        text_override: dict[str, str] | None = None,
        document_text: str | None = None,
        meta: dict[str, Any] | None = None,
    ) -> tuple[bool, str | None]:
        for chunk in chunks:
            chunk.embedding = [0.0] * 2560
        return False, None

    async def fake_extract(
        chunks: list[Chunk],
        ontology: Any,
        source_document_id: str,
        config: dict[str, Any],
        pool: Any = None,
    ) -> tuple[list[Any], list[Any], str, ExtractStats]:
        stats = ExtractStats(
            backend="stub",
            requested_entities=0,
            kept_entities=0,
            requested_relations=0,
            kept_relations=0,
        )
        return [], [], "stub", stats

    monkeypatch.setattr(ingest_common, "embed_chunks", fake_embed)
    monkeypatch.setattr(ingest_common, "extract_with_fallback", fake_extract)


# -- W5-3: the master topology migration (slug + meta index view) ------------


async def test_master_index_view_exists(tmp_path: Path) -> None:
    """A SELECT from the view answers zero rows on a fresh store."""
    path = await _migrated(tmp_path, "index-view.duckdb")
    con = duckdb.connect(str(path))
    try:
        rows = con.execute("SELECT * FROM master_project_index").fetchall()
        assert rows == []
    finally:
        con.close()
    _cleanup(path)


async def test_slug_unique_per_tenant(tmp_path: Path) -> None:
    """The slug is unique per tenant: cross-tenant reuse succeeds, reuse fails."""
    path = await _migrated(tmp_path, "slug-unique.duckdb")
    con = duckdb.connect(str(path))
    try:
        con.execute(
            "INSERT INTO research_projects (project_id, tenant_id, name, slug)"
            " VALUES ('11111111-1111-1111-1111-111111111111', ?, 'Alpha', 'alpha')",
            [TENANT],
        )
        con.execute(
            "INSERT INTO research_projects (project_id, tenant_id, name, slug)"
            " VALUES ('22222222-2222-2222-2222-222222222222', ?, 'Beta', 'alpha')",
            ["tenant-2"],
        )
        with pytest.raises(duckdb.ConstraintException):
            con.execute(
                "INSERT INTO research_projects (project_id, tenant_id, name, slug)"
                " VALUES (?, ?, ?, ?)",
                ["33333333-3333-3333-3333-333333333333", TENANT, "Gamma", "alpha"],
            )
    finally:
        con.close()
    _cleanup(path)


# -- W5-4: ingest attribution and the two envelope keys ----------------------


async def test_ingest_envelope_names_project(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A file under the workspace root names its project on the envelope."""
    from corpus_kb.tools.pipeline.plan import run_pipeline

    _stub_embed_extract(monkeypatch)
    root = tmp_path / "ws"
    slug = "uxr-alpha"
    path = await _migrated(tmp_path, "attrib-on.duckdb")
    doc_path = f"{root.as_posix()}/{slug}/x.md"
    config: dict[str, Any] = {
        "database": {"backend": "duckdb"},
        "embedding": {"model": "stub-embed", "dimensions": 2560},
        "graph": {"extract_entities": True},
        "ingest": {"contextualize": {"enabled": False}},
        "master": {"workspace_root": root.as_posix()},
    }
    gateway = await DuckDbGateway.connect(str(path))
    try:
        envelope = await run_pipeline(
            DOC_TEXT, "text", doc_path, config, gateway, TENANT, project_id=None
        )
        assert envelope["status"] == "success"
        assert envelope["project_slug"] == slug
        assert envelope["project_scope"] == "project"
        async with gateway.transaction(TENANT) as tx:
            row = await tx.fetchrow(
                "SELECT project_id, name, slug FROM research_projects WHERE slug = $1", slug
            )
        assert row is not None and row["name"] == slug
    finally:
        await gateway.close()
        _cleanup(path)


async def test_ingest_envelope_off_by_default(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No workspace root: the envelope says none, the slug is absent."""
    from corpus_kb.tools.pipeline.plan import run_pipeline

    _stub_embed_extract(monkeypatch)
    path = await _migrated(tmp_path, "attrib-off.duckdb")
    config: dict[str, Any] = {
        "database": {"backend": "duckdb"},
        "embedding": {"model": "stub-embed", "dimensions": 2560},
        "graph": {"extract_entities": True},
        "ingest": {"contextualize": {"enabled": False}},
    }
    gateway = await DuckDbGateway.connect(str(path))
    try:
        envelope = await run_pipeline(
            DOC_TEXT, "text", DOC_PATH, config, gateway, TENANT, project_id=None
        )
        assert envelope["status"] == "success"
        assert envelope["project_slug"] is None
        assert envelope["project_scope"] == "none"
    finally:
        await gateway.close()
        _cleanup(path)
