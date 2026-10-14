"""Tool-layer honesty pins for the MCP surface (zero-drift wave 1B).

``list_documents`` used to return a hardcoded empty list while the HTTP and
socket surfaces called the real ``QueryHandler``. ``delete_document`` used to
ignore its tenant argument and to claim "all related rows" were deleted.
These tests pin the fixed behavior with fakes; no database is needed.
"""

from __future__ import annotations

from typing import Any

import corpus_kb.handlers.query_handler as query_handler_module
from corpus_kb.tools.ingest_tools import list_documents

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
