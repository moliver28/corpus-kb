"""Tests for the session preflight guards in tests/conftest.py.

The guards decide skip versus run for capability-gated tests. Each probe
is cached, so these tests clear the cache around their own scenario.
"""

from __future__ import annotations

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


def test_ollama_probe_true_when_a_server_answers_version(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A server that answers /api/version with HTTP 200 yields True."""

    class _Version(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b'{"version":"0"}')

        def log_message(self, *args: object) -> None:
            pass

    server = HTTPServer(("127.0.0.1", 0), _Version)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        url = f"http://127.0.0.1:{server.server_port}"
        monkeypatch.setattr(conftest, "_ollama_base_url", lambda: url)
        assert conftest._ollama_available() is True
    finally:
        server.shutdown()


def test_ollama_skip_reason_names_the_base_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(conftest, "_ollama_base_url", lambda: "http://127.0.0.1:9")
    reason = conftest._ollama_skip_reason()
    assert "http://127.0.0.1:9" in reason
    assert "requires_ollama" in reason
