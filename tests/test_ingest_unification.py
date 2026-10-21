"""Wave-1C zero-drift tests: ONE ingest contract for every surface.

UNIFIED CONTRACT (the design this file pins)
--------------------------------------------
One ingest implementation per operation: ``tools.ingest_tools.ingest_file``
(files; binary-safe, strategy-aware) and ``tools.ingest_common.ingest_text``
(raw text; document-type whitelist). CLI, MCP, HTTP POST /api/ingest/*, and
the socket JSON-RPC ``ingest_file``/``ingest_text`` ALL go through them.

``source_type`` means ONE thing on every surface:
  - file ingest: an optional INGESTION-STRATEGY override (validated against
    ``ingestion.registry``); unknown names return an error envelope.
  - text ingest: the DOCUMENT source type, whitelisted to
    {code, markdown, text, transcript} with the same error message
    everywhere (``Invalid source_type: <value>``).
The detected document type is recorded by the pipeline in the envelope and
copied into the event-store Document aggregate, so the audit ledger stays
coherent.

Envelopes: HTTP/socket return the tool envelope EXACTLY, plus the declared
additive wrapper keys ``aggregate_id``/``aggregate_version`` (and
``event_sourcing_error`` only when event sourcing failed after a successful
pipeline). No per-surface pipeline, no per-surface envelope shapes.

The flagship envelope-parity gate lives in tests/test_surface_parity_deep.py.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any
from uuid import UUID

import pytest
from starlette.testclient import TestClient

import corpus_kb.handlers.command_handler as command_handler_module
from corpus_kb.api.http import create_http_app
from corpus_kb.api.socket import JSONRPCServer
from corpus_kb.domain.models import IngestFileCommand, IngestTextCommand

TENANT = "00000000-0000-0000-0000-000000000001"

# The declared additive HTTP/socket wrapper over the shared tool envelope.
AGGREGATE_KEYS = {"aggregate_id", "aggregate_version"}


# ---------------------------------------------------------------------------
# Stubs: pool + pipeline store + eventsourcing app (all offline)
# ---------------------------------------------------------------------------


class _LockConn:
    """Advisory-lock connection: try-lock answers True, unlocks counted."""

    def __init__(self) -> None:
        self.executes: list[str] = []

    async def fetchval(self, sql: str, *args: Any) -> bool:
        return True

    async def execute(self, sql: str, *args: Any) -> str:
        self.executes.append(sql)
        return "OK"


class _FakePool:
    def __init__(self) -> None:
        self.conn = _LockConn()

    def acquire(self) -> _FakePool:
        return self

    async def __aenter__(self) -> _LockConn:
        return self.conn

    async def __aexit__(self, *args: Any) -> bool:
        return False

    async def close(self) -> None:
        return None


class _StubStore:
    """Canned ingest store: nothing newly inserted, so embeddings never run."""

    last: _StubStore | None = None

    def __init__(self, pool: Any, tenant_id: str = "t") -> None:
        _StubStore.last = self

    async def unchanged_doc_id(self, source: str, file_hash: str) -> str | None:
        return None

    async def begin_run(self, document: Any, file_hash: str) -> tuple[str, str]:
        return "doc-1", "run-1"

    async def store_chunks(self, chunks: list[Any]) -> set[str]:
        return set()

    async def store_vectors(
        self,
        chunks: list[Any],
        embedding_model: str,
        dimensions: int = 768,
        vectors_1024: dict[str, list[float]] | None = None,
    ) -> int:
        return 0

    async def store_entities(self, entities: list[Any]) -> int:
        return 0

    async def store_relations(
        self, relations: list[Any], model_version: Any = None, prompt_version: Any = None
    ) -> int:
        return 0

    async def update_document(self, document: Any, file_hash: str) -> None:
        return None

    async def finish_run(
        self, run_id: str, file_hash: str, stages: dict[str, object], errors: list[str]
    ) -> None:
        return None

    async def fail_run(self, run_id: str, error: str) -> None:
        return None


class _SavedAggregate:
    """Records the aggregate the handler saved (ledger-coherence probe)."""

    def __init__(self) -> None:
        self.saved: list[Any] = []

    def save(self, aggregate: Any) -> None:
        self.saved.append(aggregate)


def _install_stubs(monkeypatch: pytest.MonkeyPatch) -> tuple[_FakePool, _SavedAggregate]:
    """Stub the pipeline store + eventsourcing app; return the probes.

    Resets and rebuilds the CommandHandler singleton with the offline config
    and the fake pool — exactly the seam server_wiring uses in production.
    """
    import corpus_kb.tools.ingest_common as ingest_module

    monkeypatch.setattr(ingest_module, "PostgresIngestStore", _StubStore)
    pool = _FakePool()
    saved = _SavedAggregate()
    monkeypatch.setattr(command_handler_module, "get_app_for_config", lambda _cfg: saved)
    command_handler_module.reset_command_handler()
    command_handler_module.get_command_handler(config=_ingest_config(), pool=pool)
    return pool, saved


def _ingest_config() -> dict[str, object]:
    return {
        "graph": {"extract_entities": False},
        "embedding": {"provider": "pgml"},
        "ingest": {"chunking": {"enabled": False}},
    }


# ---------------------------------------------------------------------------
# HTTP + socket ingest_file go through the shared tool path
# ---------------------------------------------------------------------------


async def _tool_envelope_keys(pool: _FakePool, path: str) -> set[str]:
    """Run the SHARED tool implementation directly and return its key set.

    The HTTP/socket assertions compare against this, so the gate fails when
    either side drifts — no hand-maintained literal to go stale.
    """
    from corpus_kb.tools.ingest_tools import ingest_file as tool_ingest_file

    result = await tool_ingest_file(path, pool, config=_ingest_config())
    assert result["status"] == "success"
    return set(result)


def test_http_ingest_file_returns_tool_envelope_plus_declared_wrapper(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pool, _saved = _install_stubs(monkeypatch)
    target = tmp_path / "note.txt"
    target.write_text("hello world", encoding="utf-8")

    client = TestClient(create_http_app())
    response = client.post("/api/ingest/file", json={"file_path": str(target)})

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "success"
    assert set(body) == asyncio.run(_tool_envelope_keys(pool, str(target))) | AGGREGATE_KEYS
    assert isinstance(body["aggregate_id"], str)
    assert isinstance(body["aggregate_version"], int)


async def test_socket_ingest_file_matches_http_envelope(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pool, _saved = _install_stubs(monkeypatch)
    target = tmp_path / "note.txt"
    target.write_text("hello world", encoding="utf-8")

    server = JSONRPCServer()
    response = await server._process_request(
        json.dumps(
            {
                "jsonrpc": "2.0",
                "id": 7,
                "method": "ingest_file",
                "params": {"file_path": str(target)},
            }
        )
    )

    body = response["result"]
    assert body["status"] == "success"
    assert set(body) == await _tool_envelope_keys(pool, str(target)) | AGGREGATE_KEYS


def test_http_ingest_file_is_binary_safe(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Bytes that are NOT valid UTF-8 no longer break the file surface: the
    strategy partitions the file, the surface never decodes it as text."""
    _install_stubs(monkeypatch)
    target = tmp_path / "blob.bin"
    target.write_bytes(b"\xff\xfe\x00\x01 not utf-8 \x9c")

    client = TestClient(create_http_app())
    response = client.post("/api/ingest/file", json={"file_path": str(target)})

    assert response.status_code == 200
    assert response.json()["status"] == "success"


def test_http_ingest_file_missing_path_is_an_error_envelope(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _install_stubs(monkeypatch)
    client = TestClient(create_http_app())
    response = client.post("/api/ingest/file", json={"file_path": str(tmp_path / "missing.txt")})
    body = response.json()
    assert body["status"] == "error"
    assert "File not found" in str(body["message"])


def test_http_ingest_file_unknown_strategy_is_an_error_envelope(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _install_stubs(monkeypatch)
    target = tmp_path / "note.txt"
    target.write_text("hello", encoding="utf-8")

    client = TestClient(create_http_app())
    response = client.post(
        "/api/ingest/file",
        json={"file_path": str(target), "source_type": "meeting-notes"},
    )
    body = response.json()
    assert body["status"] == "error"
    assert "Unknown source-type strategy 'meeting-notes'" in str(body["message"])


def test_file_source_type_is_a_strategy_and_the_event_keeps_the_document_type(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """One meaning per surface: source_type pins the STRATEGY; the event-store
    Document aggregate records the detected DOCUMENT type from the envelope."""
    _pool, saved = _install_stubs(monkeypatch)
    target = tmp_path / "call.txt"  # .txt -> document type "text"
    target.write_text("moderator: hello", encoding="utf-8")

    handler = command_handler_module.get_command_handler()
    result = asyncio.run(
        handler.handle_ingest_file(
            IngestFileCommand(tenant_id=UUID(TENANT), file_path=str(target), source_type=None)
        )
    )
    assert result["status"] == "success"
    assert result["source_type"] == "text"  # document type in the envelope
    assert len(saved.saved) == 1
    assert saved.saved[0].source_type == "text"  # ledger copies the envelope's type


# ---------------------------------------------------------------------------
# ingest_text: the whitelist is identical on every surface
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("source_type", ["web", "bogus"])
async def test_socket_ingest_text_whitelist_matches_tool_message(
    monkeypatch: pytest.MonkeyPatch, source_type: str
) -> None:
    _install_stubs(monkeypatch)
    server = JSONRPCServer()
    response = await server._process_request(
        json.dumps(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "ingest_text",
                "params": {"text": "hello", "source_type": source_type},
            }
        )
    )
    assert response["result"] == {
        "status": "error",
        "message": f"Invalid source_type: {source_type}",
    }


@pytest.mark.parametrize("source_type", ["web", "bogus"])
def test_http_ingest_text_whitelist_matches_tool_message(
    monkeypatch: pytest.MonkeyPatch, source_type: str
) -> None:
    _install_stubs(monkeypatch)
    client = TestClient(create_http_app())
    response = client.post(
        "/api/ingest/text",
        json={"text": "hello", "source_type": source_type},
    )
    assert response.status_code == 200
    assert response.json() == {
        "status": "error",
        "message": f"Invalid source_type: {source_type}",
    }


async def test_command_handler_ingest_text_matches_tool_envelope(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The socket/HTTP text path returns the SAME envelope keys as the tool
    function, plus only the declared aggregate wrapper keys."""
    from corpus_kb.tools.ingest_common import ingest_text as tool_ingest_text

    pool, saved = _install_stubs(monkeypatch)
    handler = command_handler_module.get_command_handler()

    tool_result = await tool_ingest_text(
        "hello world", pool, source_type="text", config=_ingest_config()
    )
    handler_result = await handler.handle_ingest_text(
        IngestTextCommand(
            tenant_id=UUID(TENANT), text="hello world", source="parity", source_type="text"
        )
    )

    assert handler_result["status"] == "success"
    assert set(handler_result) == set(tool_result) | AGGREGATE_KEYS
    assert len(saved.saved) == 1
