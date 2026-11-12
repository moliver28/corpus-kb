"""Behavior tests for the op_client URL guard (SSRF boundary).

The client sends to operator-configured base URLs; the guard refuses
anything that is not a plain http(s) endpoint before the request starts:
no other schemes, no credentials, a named host, and a loopback host name
that must resolve loopback. No test opens a real socket; resolution is
replaced.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

PLUGIN = str(Path(__file__).resolve().parents[1])
if PLUGIN not in sys.path:
    sys.path.insert(0, PLUGIN)

import op_client  # noqa: E402


def _resolution(address: str) -> list[tuple]:
    return [(2, 1, 6, "", (address, 0))]


@pytest.mark.parametrize(
    "url",
    [
        "ftp://127.0.0.1:8010/api/stats",
        "file:///etc/hosts",
        "http://user:pw@127.0.0.1:8010/api/stats",
        "http:///api/stats",
    ],
)
def test_validate_url_refuses_non_plain_endpoints(url: str) -> None:
    with pytest.raises(op_client.HttpError):
        op_client.validate_url(url)


def test_validate_url_accepts_the_documented_default() -> None:
    op_client.validate_url("http://127.0.0.1:8010/api/stats")
    op_client.validate_url("http://localhost:8010/api/stats")
    op_client.validate_url("http://[::1]:8010/api/stats")


def test_validate_url_accepts_a_configured_host_without_dns() -> None:
    op_client.validate_url("http://engine.internal:8010/api/stats")


def test_loopback_name_must_resolve_loopback(monkeypatch: pytest.MonkeyPatch) -> None:
    def remote_resolution(host: str, *args: object, **kwargs: object) -> list[tuple]:
        return _resolution("10.0.0.9")

    def loopback_resolution(host: str, *args: object, **kwargs: object) -> list[tuple]:
        return _resolution("::1")

    monkeypatch.setattr(op_client.socket, "getaddrinfo", remote_resolution)
    with pytest.raises(op_client.HttpError):
        op_client.validate_url("http://localhost:8010/api/stats")
    monkeypatch.setattr(op_client.socket, "getaddrinfo", loopback_resolution)
    op_client.validate_url("http://localhost:8010/api/stats")


def test_request_json_refuses_before_any_network(monkeypatch: pytest.MonkeyPatch) -> None:
    def no_network(*args: object, **kwargs: object) -> None:
        raise AssertionError("urlopen must not run for a refused URL")

    monkeypatch.setattr(op_client.urllib.request, "urlopen", no_network)
    with pytest.raises(op_client.HttpError):
        op_client.get_json("ftp://127.0.0.1:8010/api/stats")
