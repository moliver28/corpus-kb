"""Wave 4 ops: honest outcomes for every state of the topics feature.

The op layer (``tools/topics_tools.py``) is exercised at the pool/module
boundary the parity harness uses:

* disabled flag -> the honest no-op envelope (the documented dormant
  default), on BOTH ops' build side and none of the read side;
* migration 026 absent -> ``unavailable`` with the setup remedy;
* corpus below the minimum -> ``insufficient_data``;
* success path -> the envelope carries the written topics, the counts,
  the recorded params and the reduction outcome;
* missing ``[topics]`` extra -> the typed ``TopicDependencyError``
  propagates (surfaces map it to 503 / -32005 / exit 3).
"""

from __future__ import annotations

from typing import Any

import pytest

from corpus_kb.tools import topics_tools
from corpus_kb.topics.engine import EngineResult, StoredChunk, TopicBuild
from corpus_kb.topics.settings import TopicsSettings

TENANT = "00000000-0000-0000-0000-000000000001"


class _ProbeConn:
    """to_regclass truthy, writes OK, reads return the canned rows."""

    def __init__(self, embedding_rows: list[Any]) -> None:
        self._rows = embedding_rows

    async def fetchval(self, sql: str, *args: Any) -> Any:
        if "to_regclass" in sql:
            return "public.topics"
        return None

    async def execute(self, sql: str, *args: Any) -> str:
        return "OK"

    async def fetch(self, sql: str, *args: Any) -> list[Any]:
        return list(self._rows)

    def transaction(self) -> _ProbeConn:
        return self

    async def __aenter__(self) -> _ProbeConn:
        return self

    async def __aexit__(self, *args: Any) -> bool:
        return False


class _ProbePool:
    def __init__(self, rows: list[Any] | None = None) -> None:
        self._conn = _ProbeConn(rows or [])

    def acquire(self) -> _ProbePool:
        return self

    async def __aenter__(self) -> _ProbeConn:
        return self._conn

    async def __aexit__(self, *args: Any) -> bool:
        return False

    async def close(self) -> None:
        return None


class _AbsentConn(_ProbeConn):
    async def fetchval(self, sql: str, *args: Any) -> Any:
        if "to_regclass" in sql:
            return None
        return await super().fetchval(sql, *args)


class _AbsentPool(_ProbePool):
    def __init__(self) -> None:
        super().__init__()
        self._conn = _AbsentConn([])


ENABLED = {"topics": {"enabled": True, "llm_reduction": False, "min_topic_size": 2}}

# The success-path canned fit (monkeypatched over engine.build_topics).
_FIT = EngineResult(
    topics=[TopicBuild(0, "0_alpha", 2, [["signal", 0.9]])],
    assignments=[("chunk-0", 0, 0.9), ("chunk-1", 0, 0.8)],
    params={"documents": 2, "umap_random_state": 42},
    outlier_count=1,
)


def _embedding_rows(n: int) -> list[dict[str, Any]]:
    return [
        {"chunk_id": f"chunk-{i}", "text": f"text {i}", "embedding": f"[{float(i)},1.0]"}
        for i in range(n)
    ]


def _read_rows_stub(pool: _ProbePool) -> Any:
    async def _read(pg_pool: Any, tenant_id: str, limit: int = 20000) -> list[StoredChunk]:
        return [
            StoredChunk(chunk_id=str(row["chunk_id"]), text=str(row["text"]), vector=[0.1, 1.0])
            for row in pool._conn._rows
        ]

    return _read


# ---------------------------------------------------------------------------
# topics_build
# ---------------------------------------------------------------------------


async def test_build_disabled_is_an_honest_noop() -> None:
    result = await topics_tools.topics_build(_ProbePool(), config=None, tenant_id=TENANT)
    assert result["status"] == "disabled"
    assert result["topic_count"] == 0
    assert result["topics"] == []
    assert "topics.enabled" in str(result["reason"])
    assert result["remedy"]


async def test_build_without_migration_is_unavailable() -> None:
    result = await topics_tools.topics_build(_AbsentPool(), config=ENABLED, tenant_id=TENANT)
    assert result["status"] == "unavailable"
    assert "migration 026" in str(result["reason"])
    assert result["remedy"] == "run corpus-kb setup"


async def test_build_with_empty_corpus_is_insufficient_data() -> None:
    pool = _ProbePool([])  # table present, zero embedded chunks
    result = await topics_tools.topics_build(pool, config=ENABLED, tenant_id=TENANT)
    assert result["status"] == "insufficient_data"
    assert "minimum topic size" in str(result["reason"])


async def test_build_success_envelope(monkeypatch: pytest.MonkeyPatch) -> None:
    pool = _ProbePool(_embedding_rows(3))
    monkeypatch.setattr(topics_tools.engine, "read_stored_embeddings", _read_rows_stub(pool))
    monkeypatch.setattr(topics_tools.engine, "build_topics", lambda chunks, settings: _FIT)

    async def fake_write(pg_pool: Any, tenant_id: str, topics: Any, assignments: Any) -> dict[str, Any]:
        return {
            "topics_written": len(topics),
            "assignments_written": len(assignments),
            "topic_ids": {0: "topic-uuid-0"},
        }

    monkeypatch.setattr(topics_tools.writer, "write_build", fake_write)
    result = await topics_tools.topics_build(pool, config=ENABLED, tenant_id=TENANT)
    assert result["status"] == "success"
    assert result["topic_count"] == 1
    assert result["chunk_count"] == 2
    assert result["outlier_count"] == 1
    assert result["reduction"] == "off"
    assert result["params"]["umap_random_state"] == 42
    topic = result["topics"][0]
    assert topic["topic_id"] == "topic-uuid-0"
    assert topic["label"] == "0_alpha"
    assert topic["reduced_label"] is None
    assert topic["keywords"] == [["signal", 0.9]]


async def test_build_missing_extra_raises_the_typed_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from corpus_kb.errors import DependencyMissingError
    from corpus_kb.topics.engine import TopicDependencyError

    pool = _ProbePool(_embedding_rows(6))
    monkeypatch.setattr(topics_tools.engine, "read_stored_embeddings", _read_rows_stub(pool))

    def _raise(chunks: Any, settings: Any) -> EngineResult:
        raise TopicDependencyError("The topics feature needs the bertopic package.")

    monkeypatch.setattr(topics_tools.engine, "build_topics", _raise)
    with pytest.raises(DependencyMissingError) as excinfo:
        await topics_tools.topics_build(pool, config=ENABLED, tenant_id=TENANT)
    assert "corpus-kb[topics]" in excinfo.value.remediation


# ---------------------------------------------------------------------------
# topics_list
# ---------------------------------------------------------------------------


async def test_list_without_migration_is_unavailable() -> None:
    result = await topics_tools.topics_list(_AbsentPool(), config=None, tenant_id=TENANT)
    assert result["status"] == "unavailable"
    assert result["topic_count"] == 0


async def test_list_success_reads_writer_rows(monkeypatch: pytest.MonkeyPatch) -> None:
    rows = [
        {
            "topic_id": "topic-uuid-0",
            "label": "0_alpha",
            "reduced_label": "Signals",
            "size": 3,
            "keywords": [["signal", 0.9]],
            "params": {},
            "created_at": "2026-10-13T00:00:00+00:00",
        }
    ]

    async def fake_read(pg_pool: Any, tenant_id: str, limit: int = 50) -> list[dict[str, Any]]:
        assert tenant_id == TENANT
        return rows

    monkeypatch.setattr(topics_tools.writer, "read_topics", fake_read)
    result = await topics_tools.topics_list(_ProbePool(), config=None, tenant_id=TENANT)
    assert result["status"] == "success"
    assert result["topic_count"] == 1
    assert result["topics"][0]["reduced_label"] == "Signals"


async def test_list_is_not_gated_by_the_enabled_flag(monkeypatch: pytest.MonkeyPatch) -> None:
    """Reading an existing build while the flag is off stays safe."""
    async def fake_read(pg_pool: Any, tenant_id: str, limit: int = 50) -> list[dict[str, Any]]:
        return []

    monkeypatch.setattr(topics_tools.writer, "read_topics", fake_read)
    result = await topics_tools.topics_list(_ProbePool(), config=None, tenant_id=TENANT)
    assert result["status"] == "success"


async def test_list_limit_is_bounded(monkeypatch: pytest.MonkeyPatch) -> None:
    """An extreme limit clamps to MAX_LIST_LIMIT before the read."""
    seen: dict[str, int] = {}

    async def fake_read(pg_pool: Any, tenant_id: str, limit: int = 50) -> list[dict[str, Any]]:
        seen["limit"] = limit
        return []

    monkeypatch.setattr(topics_tools.writer, "read_topics", fake_read)
    result = await topics_tools.topics_list(
        _ProbePool(), config=None, tenant_id=TENANT, limit=10**9
    )
    assert result["status"] == "success"
    assert seen["limit"] == topics_tools.MAX_LIST_LIMIT
