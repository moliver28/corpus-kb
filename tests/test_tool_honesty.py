"""Tool-layer honesty pins for the MCP surface (zero-drift wave 1B).

``list_documents`` used to return a hardcoded empty list while the HTTP and
socket surfaces called the real ``QueryHandler``. ``delete_document`` used to
ignore its tenant argument and to claim "all related rows" were deleted.
These tests pin the fixed behavior with fakes; no database is needed.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any

import corpus_kb.handlers.query_handler as query_handler_module
from corpus_kb.tools.ingest_tools import delete_document, list_documents

_DOC_PAYLOAD: dict[str, object] = {
    "doc_id": "00000000-0000-0000-0000-00000000000a",
    "source": "notes.md",
    "source_type": "markdown",
    "chunk_count": 3,
    "created_at": "2026-10-13T00:00:00+00:00",
}


class _FakeDoc:
    """Stands in for a DocumentResult pydantic model."""

    def model_dump(self) -> dict[str, object]:
        return dict(_DOC_PAYLOAD)


def _fake_query_handler_class(seen: list[Any]) -> type:
    """Build a QueryHandler stand-in that records ListDocumentsQuery objects."""

    class _FakeQueryHandler:
        def __init__(self, pool: object, config: object = None) -> None:
            self.pool = pool

        async def handle_list_documents(self, query: Any) -> list[_FakeDoc]:
            seen.append(query)
            return [_FakeDoc()]

    return _FakeQueryHandler


async def test_list_documents_delegates_to_query_handler(monkeypatch: Any) -> None:
    seen: list[Any] = []
    monkeypatch.setattr(query_handler_module, "QueryHandler", _fake_query_handler_class(seen))

    result = await list_documents(
        "pool-sentinel",
        {},
        tenant_id="00000000-0000-0000-0000-000000000007",
        limit=5,
        offset=2,
    )

    assert result == {"status": "success", "result": [dict(_DOC_PAYLOAD)]}
    assert len(seen) == 1
    query = seen[0]
    assert str(query.tenant_id) == "00000000-0000-0000-0000-000000000007"
    assert query.limit == 5
    assert query.offset == 2


class _RecordingConn:
    """asyncpg connection stand-in that records execute() SQL + args."""

    def __init__(self, fail_on: str | None = None) -> None:
        self.calls: list[tuple[str, tuple[object, ...]]] = []
        self._fail_on = fail_on

    async def execute(self, sql: str, *args: object) -> str:
        self.calls.append((sql, args))
        if self._fail_on is not None and self._fail_on in sql:
            raise RuntimeError("boom-detail")
        return "OK"

    def fail_on_delete(self) -> None:
        self._fail_on = "DELETE FROM documents"


def _fake_pool(conn: _RecordingConn) -> Any:
    @asynccontextmanager
    async def _acquire() -> Any:
        yield conn

    class _FakePool:
        def acquire(self) -> Any:
            return _acquire()

    return _FakePool()


async def test_delete_document_uses_tenant_argument() -> None:
    conn = _RecordingConn()

    result = await delete_document(
        "00000000-0000-0000-0000-00000000000d",
        _fake_pool(conn),
        tenant_id="00000000-0000-0000-0000-000000000002",
    )

    assert result == {"status": "success", "document_id": "00000000-0000-0000-0000-00000000000d"}
    set_config_args = [args for sql, args in conn.calls if "set_config" in sql]
    delete_args = [args for sql, args in conn.calls if sql.startswith("DELETE")]
    assert set_config_args == [("00000000-0000-0000-0000-000000000002",)]
    assert delete_args == [("00000000-0000-0000-0000-00000000000d",)]


async def test_delete_document_defaults_to_placeholder_tenant() -> None:
    conn = _RecordingConn()

    await delete_document("00000000-0000-0000-0000-00000000000d", _fake_pool(conn))

    set_config_args = [args for sql, args in conn.calls if "set_config" in sql]
    assert set_config_args == [("00000000-0000-0000-0000-000000000001",)]


async def test_delete_document_error_names_target_and_next_step() -> None:
    conn = _RecordingConn()
    conn.fail_on_delete()

    result = await delete_document("00000000-0000-0000-0000-00000000000d", _fake_pool(conn))

    assert result["status"] == "error"
    message = str(result["message"])
    assert "00000000-0000-0000-0000-00000000000d" in message
    # The raw exception text must not leak; the message must say what failed
    # and give one fix step.
    assert "boom-detail" not in message


def test_delete_document_docstring_states_true_cascade_behavior() -> None:
    doc = delete_document.__doc__ or ""
    assert "all related rows" not in doc
    assert "cascade" in doc.lower()
    assert "NULL" in doc
    assert "persist" in doc.lower()
