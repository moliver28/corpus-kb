"""Behavior tests for the op_client URL guard (SSRF boundary).

The client sends to operator-configured base URLs; the checks refuse
anything that is not a plain http(s) endpoint before the request starts:
no other schemes, no credentials, a named host, and a loopback host name
that must resolve loopback. No test opens a real socket; ``urlopen`` and
resolution are replaced.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

PLUGIN = str(Path(__file__).resolve().parents[1])
if PLUGIN not in sys.path:
    sys.path.insert(0, PLUGIN)

import op_client  # noqa: E402


class _FakeResponse:
    def __enter__(self) -> _FakeResponse:
        return self

    def __exit__(self, *args: object) -> None:
        pass

    def read(self) -> bytes:
        return b'{"ok": true}'


def _sink_recorder(seen: list[str]):
    def fake_urlopen(req: object, timeout: float = 0.0) -> _FakeResponse:
        seen.append(str(getattr(req, "full_url", "")))
        return _FakeResponse()

    return fake_urlopen


def _resolution(address: str) -> list[tuple]:
    return [(2, 1, 6, "", (address, 0))]


def _no_network(*args: object, **kwargs: object) -> None:
    raise AssertionError("urlopen must not run for a refused URL")


@pytest.mark.parametrize(
    "url",
    [
        "ftp://127.0.0.1:8010/api/stats",
        "file:///etc/hosts",
        "http://user:pw@127.0.0.1:8010/api/stats",
        "http:///api/stats",
    ],
)
def test_refuses_non_plain_endpoints(url: str, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(op_client.urllib.request, "urlopen", _no_network)
    with pytest.raises(op_client.HttpError):
        op_client.get_json(url)


def test_sends_to_the_documented_default(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[str] = []
    monkeypatch.setattr(op_client.socket, "getaddrinfo", lambda *a, **k: _resolution("::1"))
    monkeypatch.setattr(op_client.urllib.request, "urlopen", _sink_recorder(seen))
    body = op_client.get_json("http://127.0.0.1:8010/api/stats", timeout=5.0)
    assert body == {"ok": True}
    assert seen == ["http://127.0.0.1:8010/api/stats"]


def test_sends_to_a_configured_host_without_dns(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[str] = []

    def must_not_resolve(*args: object, **kwargs: object) -> list[tuple]:
        raise AssertionError("a configured host must not need resolution")

    monkeypatch.setattr(op_client.socket, "getaddrinfo", must_not_resolve)
    monkeypatch.setattr(op_client.urllib.request, "urlopen", _sink_recorder(seen))
    body = op_client.get_json("http://engine.internal:8010/api/stats", timeout=5.0)
    assert body == {"ok": True}
    assert seen == ["http://engine.internal:8010/api/stats"]


def test_loopback_name_must_resolve_loopback(monkeypatch: pytest.MonkeyPatch) -> None:
    def remote_resolution(*args: object, **kwargs: object) -> list[tuple]:
        return _resolution("10.0.0.9")

    def loopback_resolution(*args: object, **kwargs: object) -> list[tuple]:
        return _resolution("::1")

    monkeypatch.setattr(op_client.urllib.request, "urlopen", _no_network)
    monkeypatch.setattr(op_client.socket, "getaddrinfo", remote_resolution)
    with pytest.raises(op_client.HttpError):
        op_client.get_json("http://localhost:8010/api/stats")
    seen: list[str] = []
    monkeypatch.setattr(op_client.socket, "getaddrinfo", loopback_resolution)
    monkeypatch.setattr(op_client.urllib.request, "urlopen", _sink_recorder(seen))
    assert op_client.get_json("http://localhost:8010/api/stats") == {"ok": True}
    assert seen == ["http://localhost:8010/api/stats"]
