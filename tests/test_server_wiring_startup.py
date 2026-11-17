"""Focused wiring tests for the extracted startup helpers (server_wiring).

The diff-coverage gate flagged the extracted ``_wire_*`` functions and
startup's backend gate. Each test drives ONE helper offline: heavy edges
(the graph store, the LlamaIndex backend, the SPA mount, the eventsourcing
app) are monkeypatched, and every handler/projection singleton the wiring
sets is restored afterwards, so no global leaks into other tests.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

import pytest

import corpus_kb.handlers.query_handler as query_handler_module
import corpus_kb.server_wiring as sw
from corpus_kb.errors import ConfigError
from corpus_kb.handlers.coding_handler import reset_coding_handler
from corpus_kb.handlers.command_handler import reset_command_handler
from corpus_kb.handlers.graph_handler import reset_graph_handler
from corpus_kb.handlers.idempotency import reset_idempotency_checker
from corpus_kb.handlers.router_handler import reset_router_handler
from corpus_kb.handlers.tag_handler import reset_tag_handler
from corpus_kb.handlers.versioning_handler import reset_versioning_handler
from corpus_kb.projections.checkpoint import reset_checkpoint_manager
from corpus_kb.projections.dlq import reset_dlq_handler
from corpus_kb.projections.documents_projection import reset_documents_projection
from corpus_kb.projections.embed_projection import reset_embed_projection
from corpus_kb.server_wiring import (
    HandlerWiring,
    HttpWiring,
    ProjectionWiring,
    _wire_aux_handlers,
    _wire_handlers,
    _wire_http,
    _wire_projections,
    _wire_query_stack,
    startup,
)


@pytest.fixture
def _singletons_restored(monkeypatch: pytest.MonkeyPatch):
    """Restore every singleton the wiring helpers set during a test."""
    monkeypatch.setattr(query_handler_module, "_query_handler", query_handler_module._query_handler)
    yield
    reset_router_handler()
    reset_tag_handler()
    reset_versioning_handler()
    reset_graph_handler()
    reset_coding_handler()
    reset_idempotency_checker()
    reset_checkpoint_manager()
    reset_dlq_handler()
    reset_embed_projection()
    reset_documents_projection()
    reset_command_handler()


def _wiring_config() -> dict[str, Any]:
    """Offline wiring config: no network, no optional extras."""
    return {
        "database": {"connection_string": "postgresql://offline", "backend": "postgres"},
        "search": {"reranker": "none"},
        "embedding": {"provider": "ollama", "model": "offline-model", "dimensions": 8},
    }


@pytest.mark.asyncio
async def test_wire_query_stack_returns_shared_stack(_singletons_restored) -> None:
    """The query stack returns (embedder, query handler, command handler)."""
    embedder, query_handler, command_handler = await _wire_query_stack(_wiring_config(), None)

    assert embedder is not None
    assert query_handler is not None
    assert command_handler is not None
    assert query_handler_module._query_handler is query_handler


@pytest.mark.asyncio
async def test_wire_aux_handlers_wraps_graph_tag_versioning(
    _singletons_restored, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The aux wiring builds and registers graph/tag/versioning/router/coding."""
    graph_store = SimpleNamespace(name="fake-graph-store")

    async def fake_graph_store(cfg: dict[str, Any], pool: object, backend: str) -> object:
        return graph_store

    monkeypatch.setattr(sw, "create_graph_store", fake_graph_store)
    router = await _wire_aux_handlers(_wiring_config(), None, "postgres", object(), object())

    assert router is not None
    assert router._embedder is not None


@pytest.mark.asyncio
async def test_wire_handlers_publishes_the_wiring(
    _singletons_restored, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The handler wiring returns the four published handler objects."""
    fake_app = SimpleNamespace(mapper=object())

    async def fake_graph_store(cfg: dict[str, Any], pool: object, backend: str) -> object:
        return SimpleNamespace()

    monkeypatch.setattr(sw, "create_graph_store", fake_graph_store)
    import corpus_kb.domain.application as application_module

    monkeypatch.setattr(application_module, "get_app", lambda conn_str, backend: fake_app)

    wiring = await _wire_handlers(_wiring_config(), None, "postgresql://offline", "postgres")

    assert isinstance(wiring, HandlerWiring)
    assert wiring.app is fake_app
    assert wiring.command_handler is not None
    assert wiring.query_handler is not None
    assert wiring.router_handler is not None


@pytest.mark.asyncio
async def test_wire_projections_builds_and_initializes(
    _singletons_restored, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The projection wiring builds the readers and initializes the RAG backend."""
    initialized: list[bool] = []

    class _FakeLlamaBackend:
        def __init__(self, config: dict[str, Any]) -> None:
            self.config = config

        async def initialize(self) -> None:
            initialized.append(True)

    import corpus_kb.domain.application as application_module
    import corpus_kb.storage.llamaindex_backend as llamaindex_module

    fake_app = SimpleNamespace(mapper=object())
    monkeypatch.setattr(application_module, "events_table_name", lambda app: "events_fake_table")
    monkeypatch.setattr(llamaindex_module, "LlamaIndexPostgresBackend", _FakeLlamaBackend)

    projections = await _wire_projections(
        _wiring_config(), SimpleNamespace(name="fake-pool"), fake_app
    )

    assert isinstance(projections, ProjectionWiring)
    assert projections.embed_projection is not None
    assert projections.docs_projection is not None
    assert projections.event_reader is not None
    assert projections.rag_backend is not None
    assert initialized == [True]


def test_wire_http_mounts_spa_and_socket(monkeypatch: pytest.MonkeyPatch) -> None:
    """The HTTP wiring mounts the SPA, the notebooks gate, and the socket."""
    import corpus_kb.ui.spa as spa_module

    monkeypatch.setattr(spa_module, "mount_spa", lambda app, cfg: True)
    monkeypatch.setattr(sw, "wire_notebooks", lambda app, cfg: False)
    http = _wire_http(_wiring_config())

    assert isinstance(http, HttpWiring)
    assert http.http_app is not None
    assert http.socket_server is not None
    assert http.ui_mounted is True
    assert http.notebooks_mounted is False


@pytest.mark.asyncio
async def test_startup_refuses_unknown_backend() -> None:
    """A backend that is neither postgres nor duckdb fails typed, early."""
    cfg = {"database": {"connection_string": "postgresql://x", "backend": "sqlite"}}
    with pytest.raises(ConfigError, match="postgres or duckdb"):
        await startup(cfg)


@pytest.mark.asyncio
async def test_startup_postgres_happy_path_assembles_services(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The postgres path wires handlers, projections, HTTP, and returns them."""
    pool_sentinel = SimpleNamespace(name="fake-pool")
    wired: list[str] = []

    async def fake_pool(connection_string: str) -> object:
        return pool_sentinel

    async def fake_handlers(
        cfg: dict[str, Any], pool: object, conn_str: str, backend: str
    ) -> HandlerWiring:
        wired.append("handlers")
        return HandlerWiring(
            app=SimpleNamespace(mapper=object()),
            command_handler=SimpleNamespace(name="command"),
            query_handler=SimpleNamespace(name="query"),
            router_handler=SimpleNamespace(name="router"),
        )

    async def fake_projections(cfg: dict[str, Any], pool: object, app: object) -> ProjectionWiring:
        wired.append("projections")
        return ProjectionWiring(
            embed_projection=SimpleNamespace(name="embed"),
            docs_projection=SimpleNamespace(name="docs"),
            event_reader=SimpleNamespace(name="reader"),
            rag_backend=SimpleNamespace(name="rag"),
        )

    def fake_http(cfg: dict[str, Any]) -> HttpWiring:
        wired.append("http")
        return HttpWiring(
            http_app=SimpleNamespace(name="http"),
            socket_server=SimpleNamespace(name="socket"),
            ui_mounted=True,
            notebooks_mounted=False,
        )

    monkeypatch.setattr(sw, "initialize_postgres_pool", fake_pool)
    monkeypatch.setattr(sw, "_wire_handlers", fake_handlers)
    monkeypatch.setattr(sw, "_wire_projections", fake_projections)
    monkeypatch.setattr(sw, "_wire_http", fake_http)

    # The W5-a boot guard is the fifth offline seam: an older-or-fresh
    # store passes; the probe never touches a real database here.
    async def fake_guard(_conn_str: str, _backend: str) -> None:
        return None

    monkeypatch.setattr("corpus_kb._setup.version_guard.assert_store_supported", fake_guard)

    cfg = {"database": {"connection_string": "postgresql://x", "backend": "postgres"}}
    services = await startup(cfg)

    assert wired == ["handlers", "projections", "http"]
    assert services["pool"] is pool_sentinel
    assert services["config"] is cfg
    assert services["app"].mapper is not None
    assert services["command_handler"].name == "command"
    assert services["query_handler"].name == "query"
    assert services["router_handler"].name == "router"
    assert services["embed_projection"].name == "embed"
    assert services["docs_projection"].name == "docs"
    assert services["event_reader"].name == "reader"
    assert services["rag_backend"].name == "rag"
    assert services["http_app"].name == "http"
    assert services["socket_server"].name == "socket"
    assert services["ui_mounted"] is True
    assert services["notebooks_mounted"] is False


def test_main_host_flag_binds_server_host(monkeypatch: Any, capsys: Any) -> None:
    """--host fills server.host in the config the wiring starts with."""
    seen: dict[str, Any] = {}

    async def fake_startup(cfg: dict[str, Any] | None) -> dict[str, Any]:
        seen["cfg"] = cfg
        return {"config": cfg if cfg is not None else {}}

    async def fake_run_all(services: dict[str, Any]) -> None:
        seen["services"] = services

    async def fake_shutdown(services: dict[str, Any]) -> None:
        seen["shutdown"] = True

    monkeypatch.setattr(sw, "startup", fake_startup)
    monkeypatch.setattr(sw, "run_all", fake_run_all)
    monkeypatch.setattr(sw, "shutdown", fake_shutdown)
    monkeypatch.setenv("CORPUS_KB_PORT", "8010")

    sw.main(["--transport", "http", "--port", "8010", "--host", "0.0.0.0"])

    cfg = seen["cfg"]
    assert cfg is not None
    assert cfg["server"]["host"] == "0.0.0.0"
    assert cfg["server"]["port"] == 8010
    assert seen["shutdown"] is True


def test_main_without_host_keeps_config_untouched(monkeypatch: Any) -> None:
    """No --host and an env port: the config stays None (default bind path)."""
    seen: dict[str, Any] = {}

    async def fake_startup(cfg: dict[str, Any] | None) -> dict[str, Any]:
        seen["cfg"] = cfg
        return {"config": {}}

    async def fake_run_all(services: dict[str, Any]) -> None:
        return None

    async def fake_shutdown(services: dict[str, Any]) -> None:
        return None

    monkeypatch.setattr(sw, "startup", fake_startup)
    monkeypatch.setattr(sw, "run_all", fake_run_all)
    monkeypatch.setattr(sw, "shutdown", fake_shutdown)
    monkeypatch.setenv("CORPUS_KB_PORT", "8010")

    sw.main(["--transport", "http", "--port", "8010"])

    assert seen["cfg"] is None


async def test_run_all_starts_and_stops_every_service(monkeypatch: Any) -> None:
    """run_all binds uvicorn, runs the projections, and stops everything."""
    import uvicorn

    served: dict[str, Any] = {}

    class FakeServer:
        def __init__(self, config: Any) -> None:
            served["config"] = config

        async def serve(self) -> None:
            served["served"] = True

    class FakeSocket:
        def __init__(self) -> None:
            self.started = False
            self.stopped = False

        async def start(self) -> None:
            self.started = True

        async def stop(self) -> None:
            self.stopped = True

    async def cancelled_projection(tenant: Any, reader: Any) -> None:
        raise asyncio.CancelledError

    socket = FakeSocket()
    services: dict[str, Any] = {
        "config": {"server": {"host": "127.0.0.1", "port": 8019}},
        "socket_server": socket,
        "http_app": object(),
        "ui_mounted": False,
        "embed_projection": SimpleNamespace(run=cancelled_projection),
        "docs_projection": SimpleNamespace(run=cancelled_projection),
        "event_reader": object(),
        "pool": None,
        "gateway": SimpleNamespace(),
    }
    monkeypatch.setattr(uvicorn, "Config", lambda *a, **k: SimpleNamespace())
    monkeypatch.setattr(uvicorn, "Server", FakeServer)
    import corpus_kb.queue.autorun as autorun

    monkeypatch.setattr(autorun, "start_queue_autorun", lambda pool, config: None)
    monkeypatch.delenv("CORPUS_KB_UI_OPEN", raising=False)

    await sw.run_all(services)

    assert socket.started and socket.stopped
    assert served["served"] is True
