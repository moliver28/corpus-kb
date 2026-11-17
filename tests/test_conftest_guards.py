"""Tests for the session preflight guards in tests/conftest.py.

The guards decide skip versus run for capability-gated tests. Each probe
is cached, so these tests clear the cache around their own scenario.
"""

from __future__ import annotations

import json
import os
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from tests import conftest


@pytest.fixture(autouse=True)
def _clean_caches() -> None:
    conftest._postgres_superuser_reason.cache_clear()
    conftest._ollama_available.cache_clear()
    yield
    conftest._postgres_superuser_reason.cache_clear()
    conftest._ollama_available.cache_clear()


def _tags_server(models: list[str]) -> HTTPServer:
    """A loopback stub serving /api/version and /api/tags with ``models``."""

    class _Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            if self.path == "/api/version":
                self._send(200, b'{"version":"0"}')
            elif self.path == "/api/tags":
                body = json.dumps({"models": [{"name": name} for name in models]})
                self._send(200, body.encode("utf-8"))
            else:
                self._send(404, b"{}")

        def _send(self, status: int, body: bytes) -> None:
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args: object) -> None:
            pass

    server = HTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server


def test_superuser_probe_none_on_this_host_when_postgres_works() -> None:
    """The admin role connects on a host that matches the harness contract."""
    if not conftest._postgres_reachable():
        pytest.skip("Postgres not reachable on this host")
    assert conftest._postgres_superuser_reason() is None


def test_superuser_probe_names_the_env_vars_when_the_role_is_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A failed admin connect yields a reason that guides setup (issue #358)."""
    import research_db

    monkeypatch.setattr(research_db, "SUPER_DSN", "postgresql://nobody@localhost:9/nope")
    reason = conftest._postgres_superuser_reason()
    assert reason is not None
    assert "POSTGRES_SUPERUSER" in reason
    assert "CORPUS_KB_DATABASE_URL" in reason
    assert "docs/DEVELOPMENT.md" in reason


def test_ollama_probe_false_when_the_port_is_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A closed Ollama port yields False, so marked legs skip (issue #340)."""
    monkeypatch.setattr(conftest, "_ollama_base_url", lambda: "http://127.0.0.1:9")
    assert conftest._ollama_available() is False


def test_ollama_probe_false_when_the_configured_model_is_not_served(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A port that answers without the configured model is not ready (#429).

    The stub answers both /api/version and /api/tags 200, but serves a
    different model than CORPUS_KB_EMBEDDING_MODEL names. The marked legs
    must skip: a model-less Ollama dead-ends every embed call.
    """
    server = _tags_server(["other-embedder:2b"])
    try:
        url = f"http://127.0.0.1:{server.server_port}"
        monkeypatch.setattr(conftest, "_ollama_base_url", lambda: url)
        monkeypatch.setenv("CORPUS_KB_EMBEDDING_MODEL", "test-embedder:2b")
        assert conftest._ollama_available() is False
    finally:
        server.shutdown()


def test_ollama_probe_false_when_tags_never_answer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A server with no /api/tags route (404) yields False (issue #429)."""

    class _NoTags(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            self.send_response(404)
            self.end_headers()
            self.wfile.write(b"{}")

        def log_message(self, *args: object) -> None:
            pass

    bare = HTTPServer(("127.0.0.1", 0), _NoTags)
    thread = threading.Thread(target=bare.serve_forever, daemon=True)
    thread.start()
    try:
        url = f"http://127.0.0.1:{bare.server_port}"
        monkeypatch.setattr(conftest, "_ollama_base_url", lambda: url)
        monkeypatch.setenv("CORPUS_KB_EMBEDDING_MODEL", "test-embedder:2b")
        assert conftest._ollama_available() is False
    finally:
        bare.shutdown()


def test_ollama_probe_true_when_the_configured_model_is_served(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A 200 /api/tags naming the configured model yields True."""
    server = _tags_server(["test-embedder:2b"])
    try:
        url = f"http://127.0.0.1:{server.server_port}"
        monkeypatch.setattr(conftest, "_ollama_base_url", lambda: url)
        monkeypatch.setenv("CORPUS_KB_EMBEDDING_MODEL", "test-embedder:2b")
        assert conftest._ollama_available() is True
    finally:
        server.shutdown()


def test_ollama_skip_reason_names_the_base_url_and_the_model(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The skip reason guides the novice to the exact pull command."""
    monkeypatch.setattr(conftest, "_ollama_base_url", lambda: "http://127.0.0.1:9")
    monkeypatch.setenv("CORPUS_KB_EMBEDDING_MODEL", "test-embedder:2b")
    reason = conftest._ollama_skip_reason()
    assert "http://127.0.0.1:9" in reason
    assert "requires_ollama" in reason
    assert "test-embedder:2b" in reason
    assert "ollama pull" in reason


def test_eventsourcing_pool_timeout_is_bounded(monkeypatch: pytest.MonkeyPatch) -> None:
    """conftest bounds the eventsourcing pool acquire wait (issue #429).

    The bound rides POSTGRES_CONNECT_TIMEOUT, the knob eventsourcing reads
    for its psycopg pool. Without it a dead-but-open endpoint parks the
    suite on the library default (30 s) per app construction.
    """
    monkeypatch.delenv("POSTGRES_CONNECT_TIMEOUT", raising=False)
    conftest._bound_eventsourcing_pool_timeout()
    raw = os.environ.get("POSTGRES_CONNECT_TIMEOUT")
    assert raw is not None
    assert float(raw) == 5.0
