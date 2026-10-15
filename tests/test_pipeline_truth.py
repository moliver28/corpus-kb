"""Wave 3A pipeline-truth pins: ONE directory walk, ONE delete.

``ingest_directory`` used to have two pipelines (tool walk vs handler glob
of ALL files) with two envelopes; ``delete_document`` had a direct-SQL tool
and an event-store-only HTTP path that never deleted the row. These tests
pin the unified contracts:

* the tool walk is the only walk; ``WATCH_SUFFIXES`` filters files on
  every surface; the envelope has one meaning per key;
* the command handler delegates and only adds the per-file audit events;
* the delete tool is the only row delete (tenant-honored, idempotent);
  the Deleted audit event is the handler wrapper's additive layer.

All offline: stubbed store, pool, and eventsourcing app; no Postgres.
"""

from __future__ import annotations

from typing import Any
from uuid import uuid4

import pytest
from starlette.testclient import TestClient

import corpus_kb.handlers.command_handler as command_handler_module
import corpus_kb.handlers.delete_audit as delete_audit_module
import corpus_kb.tools.ingest_common as ingest_common_module
from corpus_kb.api.http import create_http_app
from corpus_kb.domain.models import DeleteDocumentCommand, IngestDirectoryCommand
from corpus_kb.tools.ingest_tools import ingest_directory

TENANT = "00000000-0000-0000-0000-000000000001"
DOC_ID = "00000000-0000-0000-0000-00000000000d"

_DIRECTORY_ENVELOPE_KEYS = {
    "status",
    "directory",
    "files_processed",
    "total_documents",
    "total_entities",
    "results",
}

_OFFLINE_CONFIG: dict[str, Any] = {
    "graph": {"extract_entities": False},
    "embedding": {"provider": "pgml"},
    "ingest": {"chunking": {"enabled": False}},
}


class _NoopConn:
    """Connection stub: writes OK, reads empty, ingest lock free."""

    async def execute(self, sql: str, *args: object) -> str:
        return "OK"

    async def fetch(self, sql: str, *args: object) -> list[Any]:
        return []

    async def fetchrow(self, sql: str, *args: object) -> dict[str, object] | None:
        return None

    async def fetchval(self, sql: str, *args: object) -> Any:
        if "pg_try_advisory_lock" in sql:
            return True
        if "to_regclass" in sql:
            return "public.ingest_runs"
        if "information_schema.columns" in sql:
            return 2
        return 0

    def transaction(self) -> _NoopConn:
        return self

    async def __aenter__(self) -> _NoopConn:
        return self

    async def __aexit__(self, *args: object) -> bool:
        return False


class _NoopPool:
    def acquire(self) -> _NoopPool:
        return self

    async def __aenter__(self) -> _NoopConn:
        return _NoopConn()

    async def __aexit__(self, *args: object) -> bool:
        return False

    async def close(self) -> None:
        return None


class _StubStore:
    """Canned ingest store: nothing inserted, so embeddings never run."""

    def __init__(self, pool: Any, tenant_id: str = "t") -> None:
        pass

    async def unchanged_doc_id(self, source: str, file_hash: str) -> str | None:
        return None

    async def begin_run(self, document: Any, file_hash: str) -> tuple[str, str]:
        return "doc-1", "run-1"

    async def store_chunks(self, chunks: list[Any]) -> set[str]:
        return set()

    async def store_vectors(self, *args: Any, **kwargs: Any) -> int:
        return 0

    async def store_entities(self, entities: list[Any]) -> int:
        return 0

    async def store_relations(self, *args: Any, **kwargs: Any) -> int:
        return 0

    async def update_document(self, document: Any, file_hash: str) -> None:
        return None

    async def finish_run(self, *args: Any) -> None:
        return None

    async def fail_run(self, *args: Any) -> None:
        return None


class _FakeApp:
    """Stand-in eventsourcing app with a canned event history."""

    def __init__(self, history: list[Any]) -> None:
        self._history = history
        self.saved: list[Any] = []
        self.repository = self

    def get(self, aggregate_id: Any) -> list[Any]:
        return list(self._history)

    def save(self, aggregate: Any) -> None:
        self.saved.append(aggregate)


class _FakeDoc:
    """Stands in for the eventsourcing Document aggregate."""

    def __init__(self) -> None:
        self.version = 7
        self.deleted = False

    def delete(self) -> None:
        self.deleted = True

    @classmethod
    def reconstruct(cls, doc_id: Any, events: list[Any]) -> _FakeDoc:
        return cls()


@pytest.fixture
def offline_env(monkeypatch: pytest.MonkeyPatch) -> Any:
    """Stub the ingest store and the eventsourcing app; hand back the app."""
    monkeypatch.setattr(ingest_common_module, "PostgresIngestStore", _StubStore)
    app = _FakeApp(history=[])
    monkeypatch.setattr(delete_audit_module, "get_app", lambda: app)
    monkeypatch.setattr(command_handler_module, "get_app", lambda: app)
    command_handler_module.reset_command_handler()
    command_handler_module.get_command_handler(config=dict(_OFFLINE_CONFIG), pool=_NoopPool())
    yield app
    # Do not leak the stubbed singleton into later test files.
    command_handler_module.reset_command_handler()


# ---------------------------------------------------------------------------
# ingest_directory: ONE walk, one envelope
# ---------------------------------------------------------------------------


async def test_ingest_directory_walks_only_supported_files(
    tmp_path: Any, offline_env: _FakeApp
) -> None:
    (tmp_path / "notes.txt").write_text("alpha document body", encoding="utf-8")
    (tmp_path / "skipme.bin").write_bytes(b"\x00\x01not supported")
    nested = tmp_path / "nested"
    nested.mkdir()
    (nested / "guide.txt").write_text("guide body text", encoding="utf-8")

    result = await ingest_directory(str(tmp_path), _NoopPool(), config=dict(_OFFLINE_CONFIG))

    assert set(result) == _DIRECTORY_ENVELOPE_KEYS
    assert result["status"] == "success"
    assert result["directory"] == str(tmp_path)
    # The .bin file is outside WATCH_SUFFIXES: attempted files are the two
    # supported ones only.
    assert result["files_processed"] == 2
    assert [r["path"] for r in result["results"]] == [
        str(nested / "guide.txt"),
        str(tmp_path / "notes.txt"),
    ]


async def test_ingest_directory_nonrecursive_skips_nested(
    tmp_path: Any, offline_env: _FakeApp
) -> None:
    (tmp_path / "top.txt").write_text("top body", encoding="utf-8")
    nested = tmp_path / "nested"
    nested.mkdir()
    (nested / "deep.txt").write_text("deep body", encoding="utf-8")

    result = await ingest_directory(
        str(tmp_path), _NoopPool(), config=dict(_OFFLINE_CONFIG), recursive=False
    )

    assert result["files_processed"] == 1
    assert [r["path"] for r in result["results"]] == [str(tmp_path / "top.txt")]


async def test_ingest_directory_missing_dir_is_error_envelope(tmp_path: Any) -> None:
    result = await ingest_directory(str(tmp_path / "nope"), _NoopPool())

    assert result == {"status": "error", "message": f"Directory not found: {tmp_path / 'nope'}"}


async def test_command_handler_directory_delegates_and_audits_files(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Any, offline_env: _FakeApp
) -> None:
    (tmp_path / "notes.txt").write_text("alpha document body", encoding="utf-8")
    handler = command_handler_module.get_command_handler()

    cmd = IngestDirectoryCommand(tenant_id=uuid4(), directory_path=str(tmp_path))
    result = await handler.handle_ingest_directory(cmd)

    assert set(result) == _DIRECTORY_ENVELOPE_KEYS
    assert result["files_processed"] == 1
    # The wrapper added exactly one audit aggregate per successful file.
    assert len(offline_env.saved) == 1
    assert result["results"][0]["aggregate_id"]
    assert result["results"][0]["aggregate_version"] == 2


def test_http_ingest_directory_returns_tool_envelope(tmp_path: Any, offline_env: _FakeApp) -> None:
    (tmp_path / "notes.txt").write_text("alpha document body", encoding="utf-8")
    client = TestClient(create_http_app())

    response = client.post("/api/ingest/directory", json={"directory_path": str(tmp_path)})

    assert response.status_code == 200
    body = response.json()
    assert set(body) == _DIRECTORY_ENVELOPE_KEYS
    assert body["total_documents"] == 1


def test_http_ingest_directory_missing_dir_is_200_error_envelope(offline_env: Any) -> None:
    client = TestClient(create_http_app())

    response = client.post("/api/ingest/directory", json={"directory_path": "no/such/dir"})

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "error"
    assert "no/such/dir" in str(body["message"])


# ---------------------------------------------------------------------------
# delete_document: ONE delete, additive audit
# ---------------------------------------------------------------------------


async def test_delete_handler_attaches_audit_when_history_exists(
    monkeypatch: pytest.MonkeyPatch, offline_env: _FakeApp
) -> None:
    offline_env._history = [object()]
    monkeypatch.setattr(delete_audit_module, "Document", _FakeDoc)
    handler = command_handler_module.get_command_handler()

    result = await handler.handle_delete_document(DeleteDocumentCommand(doc_id=uuid4()))

    assert result["status"] == "success"
    assert result["aggregate_version"] == 7
    assert len(offline_env.saved) == 1


async def test_delete_handler_without_history_stays_clean(offline_env: _FakeApp) -> None:
    handler = command_handler_module.get_command_handler()

    result = await handler.handle_delete_document(DeleteDocumentCommand(doc_id=uuid4()))

    assert result == {"status": "success", "document_id": result["document_id"]}
    assert offline_env.saved == []


async def test_delete_audit_failure_is_recorded_not_raised(
    monkeypatch: pytest.MonkeyPatch, offline_env: _FakeApp
) -> None:
    def broken_app() -> Any:
        raise RuntimeError("event store down")

    monkeypatch.setattr(delete_audit_module, "get_app", broken_app)
    handler = command_handler_module.get_command_handler()

    result = await handler.handle_delete_document(DeleteDocumentCommand(doc_id=uuid4()))

    assert result["status"] == "success"
    assert "event store down" in str(result["event_sourcing_error"])


def test_http_delete_document_awaits_handler_and_forwards_doc_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: list[DeleteDocumentCommand] = []

    class _FakeHandler:
        async def handle_delete_document(self, cmd: DeleteDocumentCommand) -> dict[str, object]:
            seen.append(cmd)
            return {"status": "success", "document_id": str(cmd.doc_id)}

    monkeypatch.setattr(command_handler_module, "get_command_handler", lambda: _FakeHandler())
    client = TestClient(create_http_app())

    response = client.delete(f"/api/documents/{DOC_ID}")

    assert response.status_code == 200
    assert response.json() == {"status": "success", "document_id": DOC_ID}
    assert [str(cmd.doc_id) for cmd in seen] == [DOC_ID]
    # The route sends no tenant, so the command carries the RLS default.
    assert str(seen[0].tenant_id) == TENANT
