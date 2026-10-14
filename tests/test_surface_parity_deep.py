"""Wave-1C flagship gate: envelope parity across shared tool / HTTP / socket.

The CATALOGUE below is the seed registry of surface operations. For each
entry, the SAME arguments are sent through three independent legs:

  (a) ``shared``  — the ONE shared function every surface must route
      through (the tool layer; for ``search`` there is no tool wrapper yet,
      so the leg pins the shared envelope builder the HTTP and socket
      surfaces both call — Wave 2 grows this into a real tool function);
  (b) ``http``    — the Starlette route via TestClient (stubbed pool);
  (c) ``socket``  — the JSON-RPC request-processing entry point.

The test asserts the response ENVELOPE KEY-SETS are equal, modulo the
per-surface ALLOWED_EXTRA declared in the catalogue (today only the
HTTP/socket ingest wrapper keys ``aggregate_id``/``aggregate_version``).
A key added on ONE surface only fails with a printed diff naming the
drifting keys. Fully offline: the DB layer is stubbed at the same seams
tests/test_ingest_integrity.py and tests/test_search_honesty.py use.

Wave 2: append catalogue entries (never edit the assertion) to grow it.
"""

from __future__ import annotations

import json
from typing import Any
from uuid import UUID

import pytest
from starlette.testclient import TestClient

import corpus_kb.handlers.command_handler as command_handler_module
import corpus_kb.handlers.query_handler as query_handler_module
import corpus_kb.tools.ingest_common as ingest_common_module
from corpus_kb.api.http import create_http_app
from corpus_kb.api.socket import JSONRPCServer
from corpus_kb.domain.models import SearchQuery
from corpus_kb.handlers.query_handler import QueryHandler
from corpus_kb.handlers.query_integrity import (
    DegradedExcluded,
    SearchEnvelope,
    envelope_to_payload,
)
from corpus_kb.tools.ingest_tools import list_documents as list_documents_tool

TENANT = "00000000-0000-0000-0000-000000000001"

# Declared additive HTTP/socket wrapper over the shared tool envelope.
AGGREGATE_KEYS = {"aggregate_id", "aggregate_version"}

TEXT_BODY = "Corpus parity seed document. " * 4


# ---------------------------------------------------------------------------
# Shared-leg implementations (the ONE function each surface must route via)
# ---------------------------------------------------------------------------


async def _shared_ingest_text(ctx: dict[str, Any]) -> dict[str, object]:
    return await ingest_common_module.ingest_text(
        TEXT_BODY,
        ctx["pool"],
        source_type="text",
        config=ctx["config"],
        source="parity-seed",
    )


async def _shared_search(ctx: dict[str, Any]) -> dict[str, object]:
    envelope = await ctx["query_handler"].handle_search_with_meta(
        SearchQuery(tenant_id=UUID(TENANT), query="parity probe", k=3)
    )
    return envelope_to_payload(envelope)


async def _shared_list_documents(ctx: dict[str, Any]) -> dict[str, object]:
    return await list_documents_tool(ctx["pool"], ctx["config"])


CATALOGUE: list[dict[str, Any]] = [
    {
        "op": "ingest_text",
        "shared": _shared_ingest_text,
        "http": {
            "method": "post",
            "path": "/api/ingest/text",
            "json": {"text": TEXT_BODY, "source": "parity-seed", "source_type": "text"},
        },
        "socket": ("ingest_text", {"text": TEXT_BODY, "source": "parity-seed"}),
        "allowed_extra": {"http": AGGREGATE_KEYS, "socket": AGGREGATE_KEYS, "shared": set()},
    },
    {
        "op": "search",
        "shared": _shared_search,
        "http": {
            "method": "post",
            "path": "/api/search",
            "json": {"query": "parity probe", "k": 3},
        },
        "socket": ("search", {"query": "parity probe", "k": 3}),
        "allowed_extra": {"http": set(), "socket": set(), "shared": set()},
    },
    {
        "op": "list_documents",
        "shared": _shared_list_documents,
        "http": {"method": "get", "path": "/api/documents", "params": {"limit": 5, "offset": 0}},
        "socket": ("list_documents", {"limit": 5, "offset": 0}),
        "allowed_extra": {"http": set(), "socket": set(), "shared": set()},
    },
]


# ---------------------------------------------------------------------------
# Offline stubs (pool / pipeline store / eventsourcing app / query handler)
# ---------------------------------------------------------------------------


class _LockConn:
    async def fetchval(self, sql: str, *args: Any) -> bool:
        return True

    async def execute(self, sql: str, *args: Any) -> str:
        return "OK"


class _FakePool:
    def acquire(self) -> _FakePool:
        return self

    async def __aenter__(self) -> _LockConn:
        return _LockConn()

    async def __aexit__(self, *args: Any) -> bool:
        return False

    async def close(self) -> None:
        return None


class _StubStore:
    """Canned ingest store: nothing newly inserted, so embeddings never run."""

    def __init__(self, pool: Any, tenant_id: str = "t") -> None:
        self.unchanged: str | None = None

    async def unchanged_doc_id(self, source: str, file_hash: str) -> str | None:
        return self.unchanged

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
    def __init__(self) -> None:
        self.saved: list[Any] = []

    def save(self, aggregate: Any) -> None:
        self.saved.append(aggregate)


class _StubQueryHandler:
    """The read-side handler stand-in for the HTTP/socket query surfaces."""

    async def handle_search_with_meta(self, query: Any) -> SearchEnvelope:
        return SearchEnvelope(status="success", results=[], degraded_excluded=DegradedExcluded())

    async def handle_list_documents(self, query: Any) -> list[Any]:
        return []


def _offline_config() -> dict[str, object]:
    return {
        "graph": {"extract_entities": False},
        "embedding": {"provider": "pgml"},
        "ingest": {"hierarchy": {"enabled": False}},
    }


@pytest.fixture
def parity_env(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """One stubbed world for all three legs, torn down per test."""
    monkeypatch.setattr(ingest_common_module, "PostgresIngestStore", _StubStore)
    pool = _FakePool()
    saved = _SavedAggregate()
    monkeypatch.setattr(command_handler_module, "get_app", lambda: saved)
    command_handler_module.reset_command_handler()
    command_handler_module.get_command_handler(config=_offline_config(), pool=pool)

    query_handler = _StubQueryHandler()
    monkeypatch.setattr(query_handler_module, "get_query_handler", lambda pool=None: query_handler)

    async def canned_list_documents(self: Any, query: Any) -> list[Any]:
        return []

    monkeypatch.setattr(QueryHandler, "handle_list_documents", canned_list_documents)

    yield {
        "pool": pool,
        "config": _offline_config(),
        "query_handler": query_handler,
        "client": TestClient(create_http_app()),
        "server": JSONRPCServer(),
    }
    command_handler_module.reset_command_handler()


# ---------------------------------------------------------------------------
# The gate
# ---------------------------------------------------------------------------


def _assert_parity(op: str, keys: dict[str, set[str]], allowed: dict[str, set[str]]) -> None:
    """Fail loudly, printing the exact drifting keys per surface."""
    base = keys["shared"]
    problems = []
    for surface in ("http", "socket"):
        surface_keys = keys[surface]
        missing = sorted(base - surface_keys)
        unexpected = sorted((surface_keys - base) - allowed.get(surface, set()))
        if missing or unexpected:
            problems.append(
                f"{surface}: missing vs shared = {missing}, "
                f"unexpected vs shared = {unexpected} "
                f"(allowed extra: {sorted(allowed.get(surface, set()))})"
            )
    assert not problems, f"envelope drift on {op!r}:\n" + "\n".join(problems)


@pytest.mark.parametrize("entry", CATALOGUE, ids=lambda entry: entry["op"])
async def test_envelope_parity(entry: dict[str, Any], parity_env: dict[str, Any]) -> None:
    ctx = parity_env
    client: TestClient = ctx["client"]
    server: JSONRPCServer = ctx["server"]

    shared_env = await entry["shared"](ctx)
    assert isinstance(shared_env, dict)

    spec = entry["http"]
    if spec["method"] == "post":
        http_resp = client.post(spec["path"], json=spec["json"])
    else:
        http_resp = client.get(spec["path"], params=spec["params"])
    assert http_resp.status_code == 200, http_resp.text
    http_env = http_resp.json()

    socket_method, socket_params = entry["socket"]
    socket_resp = await server._process_request(
        json.dumps({"jsonrpc": "2.0", "id": 1, "method": socket_method, "params": socket_params})
    )
    assert "result" in socket_resp, socket_resp
    socket_env = socket_resp["result"]

    keys = {
        "shared": set(shared_env),
        "http": set(http_env),
        "socket": set(socket_env),
    }
    _assert_parity(entry["op"], keys, entry["allowed_extra"])
