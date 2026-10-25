"""Meta-embedding builder + store tests (Living Corpus W6)."""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any

import numpy as np
import pytest

from corpus_kb.analysis.meta_build import MetaRow, _centroid, _group_centroids
from corpus_kb.tools.meta_embed_tools import _parse_kinds, analysis_embed_meta


def test_centroid_normalizes() -> None:
    vectors = [np.array([3.0, 0.0]), np.array([0.0, 1.0])]
    centroid = _centroid(vectors)
    assert centroid is not None
    norm = float(np.linalg.norm(np.array(centroid)))
    assert abs(norm - 1.0) < 1e-5


def test_centroid_skips_zero_mass() -> None:
    assert _centroid([]) is None
    assert _centroid([np.zeros(4), np.zeros(4)]) is None


def test_group_centroids_groups_and_sorts() -> None:
    vectors = {
        "c1": np.array([1.0, 0.0]),
        "c2": np.array([0.9, 0.1]),
        "c3": np.array([0.0, 1.0]),
    }
    mapping = {
        "c1": ("doc-b", "b"),
        "c2": ("doc-a", "a"),
        "c3": ("doc-a", "a"),
    }
    rows = _group_centroids("document", vectors, mapping)
    assert [row.meta_key for row in rows] == ["doc-a", "doc-b"]
    assert [row.source_count for row in rows] == [2, 1]


def test_parse_kinds_default_all_and_validation() -> None:
    assert _parse_kinds(None) == ["belief_summary", "document", "profile", "topic"]
    assert _parse_kinds("all") == ["belief_summary", "document", "profile", "topic"]
    assert _parse_kinds("topic, document") == ["document", "topic"]
    with pytest.raises(Exception, match="Unknown meta kinds"):
        _parse_kinds("topic, tarot")


class _FakeConn:
    async def fetch(self, sql: str, *args: Any) -> list[dict[str, Any]]:
        if "FROM chunk_topics ct" in sql:
            return [
                {"chunk_id": "c1", "topic_id": "t1", "label": "desserts"},
                {"chunk_id": "c2", "topic_id": "t1", "label": "desserts"},
            ]
        if "FROM chunks c JOIN documents d" in sql:
            return [
                {"chunk_id": "c1", "doc_id": "d1", "label": "a.md"},
                {"chunk_id": "c2", "doc_id": "d1", "label": "a.md"},
            ]
        return []

    async def __aenter__(self) -> _FakeConn:
        return self

    async def __aexit__(self, *args: Any) -> bool:
        return False


class _FakePool:
    def acquire(self) -> _FakeConn:
        return _FakeConn()


class _FakeEngineConn(_FakeConn):
    """classification read path: keyset batches of stored vectors."""

    async def fetch(self, sql: str, *args: Any) -> list[dict[str, Any]]:
        if "embedding IS NOT NULL" in sql or "cv.chunk_id" in sql:
            return [
                {"chunk_id": "c1", "text": "one", "embedding": "[1.0,0.0]"},
                {"chunk_id": "c2", "text": "two", "embedding": "[0.0,1.0]"},
            ]
        return await super().fetch(sql, *args)


class _FakeEnginePool:
    def acquire(self) -> _FakeEngineConn:
        return _FakeEngineConn()


async def test_analysis_embed_meta_envelope(monkeypatch: pytest.MonkeyPatch) -> None:
    import corpus_kb.classification.engine as engine

    async def fake_read(pool: Any, tenant: str) -> list[Any]:
        return [
            engine.StoredVector("c1", "one", np.array([1.0, 0.0], dtype=np.float32)),
            engine.StoredVector("c2", "two", np.array([0.0, 1.0], dtype=np.float32)),
        ]

    async def fake_upsert(
        pool: Any, tenant: str, rows: list[MetaRow], *, is_duckdb: bool = False
    ) -> int:
        return len(rows)

    @asynccontextmanager
    async def fake_tenant_conn(pool: Any, tenant: str) -> Any:
        async with pool.acquire() as conn:
            yield conn

    monkeypatch.setattr(engine, "read_stored_vectors", fake_read)
    monkeypatch.setattr(engine, "tenant_connection", fake_tenant_conn)
    monkeypatch.setattr("corpus_kb.analysis.meta_store.upsert_meta_rows", fake_upsert)
    result = await analysis_embed_meta(
        _FakePool(), {}, "00000000-0000-0000-0000-000000000001", kinds="document"
    )
    assert result["status"] == "success"
    assert set(result) >= {"status", "kinds", "built", "per_kind", "notes"}
    assert result["kinds"] == ["document"]
    assert result["built"] == 1
    assert result["per_kind"] == {"document": 1}


async def test_analysis_embed_meta_unknown_kind_fails() -> None:
    with pytest.raises(Exception, match="Unknown meta kinds"):
        await analysis_embed_meta(
            _FakePool(), {}, "00000000-0000-0000-0000-000000000001", kinds="tarot"
        )
