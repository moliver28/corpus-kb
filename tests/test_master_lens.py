"""The W5-5 project lens: parser isolation, tenant binding, scope routing.

The "project" Predicate target is PIPELINE-INTERNAL: the parser never
emits it, and the SQL branch is tenant-bound inside the EXISTS subquery.
Pure tests run on build_filter_sql; the routing test rides a DuckDB
scratch store with the search arms faked (the lens + stamp run for real).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

pytest.importorskip("duckdb")

from corpus_kb.master.state_store import note_activity, project_id_for_slug, resolve_lens
from corpus_kb.rag.self_query import Predicate, build_filter_sql
from corpus_kb.storage.duckdb_support import duckdb_path_to_conn_string
from corpus_kb.storage.gateway import DuckDbGateway

TENANT = "00000000-0000-0000-0000-000000000001"
NOW = datetime(2026, 11, 25, 12, 0, 0, tzinfo=UTC)
CFG = {"master": {"workspace_root": "W:/ws", "switch_window_s": 1800, "idle_window_s": 7200}}


async def _migrated(tmp_path: Path, name: str) -> Path:
    from corpus_kb._setup.migrate import run_migrations

    path = tmp_path / name
    await run_migrations(duckdb_path_to_conn_string(path))
    return path


def test_scope_predicate_is_tenant_bound() -> None:
    """One bound parameter; the tenant match sits INSIDE the subquery."""
    frag, params = build_filter_sql(
        [Predicate(target="project", op="=", value="pid-9")], start_index=4
    )
    assert params == ["pid-9"]
    assert "pc.tenant_id" in frag
    assert "chunks.doc_id" in frag
    assert "$4" in frag


def test_tag_branch_fully_qualified() -> None:
    """The tag and metadata arms reference chunks.doc_id, never a c alias."""
    frag, _ = build_filter_sql(
        [
            Predicate(target="tag", op="=", value="t1"),
            Predicate(target="metadata", op="=", value="v", key="k"),
        ],
        start_index=4,
    )
    assert "chunks.doc_id" in frag
    assert "c.doc_id" not in frag


def test_parser_rejects_project_target(monkeypatch: pytest.MonkeyPatch) -> None:
    """The parser allowlist never passes target "project" through."""
    from corpus_kb.rag.self_query import SelfQueryParser

    parser = SelfQueryParser()

    def fake_chat(self: object, query: str) -> str:
        import json

        return json.dumps(
            {
                "semantic_query": "q",
                "predicates": [
                    {"target": "project", "op": "=", "value": "pid-9"},
                    {"target": "tag", "op": "=", "value": "ok"},
                ],
            }
        )

    monkeypatch.setattr(SelfQueryParser, "_client_chat", fake_chat)
    parsed = parser.parse("anything")
    targets = [p.target for p in parsed.predicates]
    assert "project" not in targets
    assert "tag" in targets


async def test_scope_default_rides_active_project(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """scope=None rides the active project; scope=all is pool-wide."""
    from corpus_kb.domain.models import SearchQuery
    from corpus_kb.handlers import query_pipeline

    path = await _migrated(tmp_path, "lens-route.duckdb")
    gateway = await DuckDbGateway.connect(str(path))
    captured: dict[str, Any] = {}

    async def fake_arm(pool: object, backend: object, run: Any, hnsw: object) -> object:
        captured["predicates"] = list(run.dynamic_predicates)
        return object()

    async def fake_legs(*a: object, **k: object) -> tuple[list[object], list[object]]:
        return [], []

    async def fake_post(*a: object, **k: object) -> list[object]:
        return []

    def fake_final(
        outcome: object,
        results: list[object],
        run: object,
        query_rewrites: object = None,
        project_scope: str = "all",
        active_project: str | None = None,
    ) -> object:
        captured["scope"] = project_scope
        captured["slug"] = active_project
        return object()

    monkeypatch.setattr(query_pipeline, "arm_outcome", fake_arm)
    monkeypatch.setattr(query_pipeline, "rewrite_legs", fake_legs)
    monkeypatch.setattr(query_pipeline, "post_fusion", fake_post)
    monkeypatch.setattr(query_pipeline, "final_envelope", fake_final)
    monkeypatch.setenv("CORPUS_KB_INSTALL_DATA_DIR", str(tmp_path / "data"))

    async def run_search(scope: str | None) -> dict[str, Any]:
        return await query_pipeline.search_with_meta(
            pool=gateway,
            search_backend=None,
            reranker=None,
            rerank_settings=SimpleNamespace(return_top_k=5),
            retrieval_settings=SimpleNamespace(lexical_candidates=20, vector_candidates=20),
            hnsw_settings=SimpleNamespace(),
            exclude_degraded=False,
            self_query_parser=None,
            self_query_enabled=False,
            query=SearchQuery(tenant_id=uuid.UUID(TENANT), query="x", scope=scope),
            config=CFG,
        )

    try:
        pid = await project_id_for_slug(gateway, TENANT, "alpha")
        # Seed against the REAL clock: the search path stamps with
        # datetime.now(), and future-dated activity reads as stale to the
        # active-state math (age turns negative -> pool mode).
        recent = datetime.now(UTC) - timedelta(seconds=60)
        await note_activity(gateway, TENANT, pid, "ingest", recent)
        await resolve_lens(gateway, CFG, TENANT, datetime.now(UTC))
        await run_search(None)
        project_preds = [p for p in captured["predicates"] if p.target == "project"]
        assert [(p.op, p.value) for p in project_preds] == [("=", pid)]
        assert captured["scope"] == "alpha"
        assert captured["slug"] == "alpha"
        await run_search("all")
        assert [p for p in captured["predicates"] if p.target == "project"] == []
        assert captured["scope"] == "all"
        await run_search("alpha")
        project_preds = [p for p in captured["predicates"] if p.target == "project"]
        assert [p.value for p in project_preds] == [pid]
        assert captured["scope"] == "alpha"
        # Unknown slug: pool-wide with the honest "all" stamp (the promise
        # in the query_pipeline comment; ProjectNotFoundError is spec s11).
        await run_search("does-not-exist")
        assert [p for p in captured["predicates"] if p.target == "project"] == []
        assert captured["scope"] == "all"
    finally:
        await gateway.close()
        for suffix in ("", ".wal"):
            Path(f"{path}{suffix}").unlink(missing_ok=True)
