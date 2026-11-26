"""The active-state store (W5-2): gateway contracts on both engines.

DuckDB legs run on a scratch database migrated from the duckdb set; the
Postgres leg rides pg_pool and skips when Postgres is unreachable. The
sidecar writes go to a temp data dir, never the real home folder. Reads
run through the gateway transaction (a raw duckdb.connect cannot open a
file the gateway already holds).
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

pytest.importorskip("duckdb")

from corpus_kb._setup.migrate import run_migrations
from corpus_kb.master.state_store import (
    note_activity,
    project_id_for_slug,
    resolve_lens,
    sidecar_path,
    stamp_project_touch,
)
from corpus_kb.storage.duckdb_support import duckdb_path_to_conn_string
from corpus_kb.storage.gateway import DuckDbGateway

TENANT = "tenant-1"
NOW = datetime(2026, 11, 25, 12, 0, 0, tzinfo=UTC)
CFG = {"master": {"workspace_root": "W:/ws", "switch_window_s": 1800, "idle_window_s": 7200}}


async def _migrated(tmp_path: Path, name: str) -> Path:
    path = tmp_path / name
    await run_migrations(duckdb_path_to_conn_string(path))
    return path


def _cleanup(path: Path) -> None:
    for suffix in ("", ".wal"):
        Path(f"{path}{suffix}").unlink(missing_ok=True)


async def test_note_activity_upserts(tmp_path: Path) -> None:
    """The second stamp for one kind updates the row instead of adding one."""
    path = await _migrated(tmp_path, "note.duckdb")
    gateway = await DuckDbGateway.connect(str(path))
    try:
        pid = await project_id_for_slug(gateway, TENANT, "alpha")
        await note_activity(gateway, TENANT, pid, "ingest", NOW)
        await note_activity(gateway, TENANT, pid, "ingest", NOW + timedelta(seconds=5))
        async with gateway.transaction(TENANT) as tx:
            rows = await tx.fetch("SELECT kind, last_ts FROM project_activity")
        assert len(rows) == 1
        assert rows[0]["kind"] == "ingest"
        assert rows[0]["last_ts"] == NOW + timedelta(seconds=5)
    finally:
        await gateway.close()
        _cleanup(path)


async def test_project_id_for_slug_registers_unknown(tmp_path: Path) -> None:
    """An unknown slug auto-registers one row named after the slug; a known one answers its id."""
    path = await _migrated(tmp_path, "register.duckdb")
    gateway = await DuckDbGateway.connect(str(path))
    try:
        first = await project_id_for_slug(gateway, TENANT, "alpha")
        second = await project_id_for_slug(gateway, TENANT, "alpha")
        assert first == second
        async with gateway.transaction(TENANT) as tx:
            rows = await tx.fetch(
                "SELECT project_id, name, slug FROM research_projects WHERE tenant_id = $1",
                TENANT,
            )
        assert [(str(r["project_id"]), r["name"], r["slug"]) for r in rows] == [
            (first, "alpha", "alpha")
        ]
    finally:
        await gateway.close()
        _cleanup(path)


async def test_resolve_lens_round_trip(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Activity resolves to the active project; the cache row and sidecar follow."""
    monkeypatch.setenv("CORPUS_KB_INSTALL_DATA_DIR", str(tmp_path))
    path = await _migrated(tmp_path, "lens.duckdb")
    gateway = await DuckDbGateway.connect(str(path))
    try:
        pid = await project_id_for_slug(gateway, TENANT, "alpha")
        await note_activity(gateway, TENANT, pid, "ingest", NOW - timedelta(seconds=60))
        active_id, slug = await resolve_lens(gateway, CFG, TENANT, NOW)
        assert active_id == pid
        assert slug == "alpha"
        async with gateway.transaction(TENANT) as tx:
            cache = await tx.fetch(
                "SELECT active_project_id FROM master_state WHERE tenant_id = $1", TENANT
            )
        assert [str(r["active_project_id"]) for r in cache] == [pid]
        sidecar = json.loads(sidecar_path(CFG).read_text(encoding="utf-8"))
        assert sidecar["active_project"] == "alpha"
        assert "updated_ts" in sidecar
    finally:
        await gateway.close()
        _cleanup(path)


async def test_stamp_project_touch_records_kind(tmp_path: Path) -> None:
    """A touch stamps the CACHED active project only — no recompute, no seed."""
    path = await _migrated(tmp_path, "touch.duckdb")
    gateway = await DuckDbGateway.connect(str(path))
    try:
        pid = await project_id_for_slug(gateway, TENANT, "alpha")
        async with gateway.transaction(TENANT) as tx:
            await tx.execute(
                "INSERT INTO master_state (tenant_id, active_project_id, updated_ts)"
                " VALUES ($1, $2, $3)",
                TENANT,
                pid,
                NOW,
            )
        await stamp_project_touch(gateway, CFG, TENANT, "query", NOW)
        # No cached project under this tenant: nothing lands.
        await stamp_project_touch(gateway, CFG, "tenant-x", "query", NOW)
        async with gateway.transaction(TENANT) as tx:
            rows = await tx.fetch(
                "SELECT project_id, kind FROM project_activity WHERE tenant_id = $1", TENANT
            )
            empty = await tx.fetchval(
                "SELECT count(*) FROM project_activity WHERE tenant_id = $1", "tenant-x"
            )
        assert [(str(r["project_id"]), r["kind"]) for r in rows] == [(pid, "query")]
        assert empty == 0
    finally:
        await gateway.close()
        _cleanup(path)


async def test_resolve_lens_answers_none_without_activity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No rows and no cache: the pool state, with no cache write and no sidecar."""
    monkeypatch.setenv("CORPUS_KB_INSTALL_DATA_DIR", str(tmp_path / "none-data"))
    path = await _migrated(tmp_path, "none.duckdb")
    gateway = await DuckDbGateway.connect(str(path))
    try:
        active_id, slug = await resolve_lens(gateway, CFG, TENANT, NOW)
        assert active_id is None
        assert slug is None
        async with gateway.transaction(TENANT) as tx:
            count = await tx.fetchval("SELECT count(*) FROM master_state")
        assert count == 0
    finally:
        await gateway.close()
        _cleanup(path)


async def test_resolve_lens_pg(pg_pool) -> None:
    """The same lens contract on Postgres: RLS-scoped rows, one cached project."""
    from corpus_kb.storage.gateway import PostgresGateway

    gateway = PostgresGateway(pg_pool)
    tenant = str(uuid.uuid4())
    pid = await project_id_for_slug(gateway, tenant, "alpha")
    await note_activity(gateway, tenant, pid, "ingest", NOW - timedelta(seconds=60))
    active_id, slug = await resolve_lens(gateway, CFG, tenant, NOW)
    assert active_id == pid
    assert slug == "alpha"


# -- W5-5: the query and analysis ops stamp their activity kind --------------


def _fake_search_arms(monkeypatch: pytest.MonkeyPatch) -> dict:
    """Fake the search arms so search_with_meta runs lens + stamps for real."""
    from corpus_kb.handlers import query_pipeline

    captured: dict = {}

    async def fake_arm(pool, backend, run, hnsw):
        captured["predicates"] = list(run.dynamic_predicates)
        return object()

    async def fake_legs(*a, **k):
        return [], []

    async def fake_post(*a, **k):
        return []

    def fake_final(
        outcome, results, run, query_rewrites=None, project_scope="all", active_project=None
    ):
        captured["scope"] = project_scope
        captured["slug"] = active_project
        return object()

    monkeypatch.setattr(query_pipeline, "arm_outcome", fake_arm)
    monkeypatch.setattr(query_pipeline, "rewrite_legs", fake_legs)
    monkeypatch.setattr(query_pipeline, "post_fusion", fake_post)
    monkeypatch.setattr(query_pipeline, "final_envelope", fake_final)
    return captured


async def test_search_stamps_query_activity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A lensed search stamps kind "query" on the project_activity row."""
    import uuid as uuid_mod
    from types import SimpleNamespace

    from corpus_kb.domain.models import SearchQuery
    from corpus_kb.handlers import query_pipeline

    captured = _fake_search_arms(monkeypatch)
    monkeypatch.setenv("CORPUS_KB_INSTALL_DATA_DIR", str(tmp_path / "data"))
    path = await _migrated(tmp_path, "stamp-query.duckdb")
    gateway = await DuckDbGateway.connect(str(path))
    tenant = "00000000-0000-0000-0000-000000000001"
    try:
        pid = await project_id_for_slug(gateway, tenant, "alpha")
        recent = datetime.now(UTC) - timedelta(seconds=60)
        await note_activity(gateway, tenant, pid, "ingest", recent)
        await resolve_lens(gateway, CFG, tenant, datetime.now(UTC))
        await query_pipeline.search_with_meta(
            pool=gateway,
            search_backend=None,
            reranker=None,
            rerank_settings=SimpleNamespace(return_top_k=5),
            retrieval_settings=SimpleNamespace(lexical_candidates=20, vector_candidates=20),
            hnsw_settings=SimpleNamespace(),
            exclude_degraded=False,
            self_query_parser=None,
            self_query_enabled=False,
            query=SearchQuery(tenant_id=uuid_mod.UUID(tenant), query="x", scope=None),
            config=CFG,
        )
        async with gateway.transaction(tenant) as tx:
            rows = await tx.fetch("SELECT kind FROM project_activity WHERE tenant_id = $1", tenant)
        kinds = sorted(r["kind"] for r in rows)
        assert "query" in kinds
        assert captured["scope"] == "alpha"
    finally:
        await gateway.close()
        _cleanup(path)


async def test_analyze_run_stamps_analysis_activity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """analyze_run stamps kind "analysis" on the cached active project."""
    from corpus_kb.tools import analysis_tools

    monkeypatch.setenv("CORPUS_KB_INSTALL_DATA_DIR", str(tmp_path / "data"))
    path = await _migrated(tmp_path, "stamp-analysis.duckdb")
    gateway = await DuckDbGateway.connect(str(path))

    async def fake_section(pool, config, tenant_id, settings=None):
        from corpus_kb.analysis.report import disabled_section

        return disabled_section("dates", "x", "y")

    monkeypatch.setattr(analysis_tools, "_dates_section", fake_section)
    monkeypatch.setattr(analysis_tools, "_labeling_section", fake_section)
    monkeypatch.setattr(analysis_tools, "_classification_section", fake_section)
    monkeypatch.setattr(analysis_tools, "_trends_section", fake_section)
    config = {"master": {"workspace_root": "W:/ws"}, "analysis": {"labeling": {"enabled": True}}}
    try:
        pid = await project_id_for_slug(gateway, TENANT, "alpha")
        async with gateway.transaction(TENANT) as tx:
            await tx.execute(
                "INSERT INTO master_state (tenant_id, active_project_id, updated_ts)"
                " VALUES ($1, $2, $3)",
                TENANT,
                pid,
                NOW,
            )
        await analysis_tools.analyze_run(gateway, config=config, tenant_id=TENANT)
        async with gateway.transaction(TENANT) as tx:
            kinds = await tx.fetch("SELECT kind FROM project_activity WHERE tenant_id = $1", TENANT)
        assert sorted(r["kind"] for r in kinds) == ["analysis"]
    finally:
        await gateway.close()
        _cleanup(path)
