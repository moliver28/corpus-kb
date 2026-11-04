"""Per-class socket codes: every CorpusError subclass answers socket_code.

Issue #304. The socket dispatch answered -32603 (internal error) for
every taxonomy class except ValidationError. The wire contract says one
failure class maps to ONE socket code. These tests pin each class code
and the unchanged JSON-RPC frames: -32600 parse, -32601 method, success.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

import corpus_kb.api.socket_ops as socket_ops_module
from corpus_kb.api.socket import JSONRPCServer
from corpus_kb.errors import (
    ConfigError,
    ConflictError,
    CorpusError,
    DatabaseUnavailableError,
    DependencyMissingError,
    InternalError,
    MigrationMissingError,
    NotFoundError,
    PayloadTooLargeError,
    UnsupportedMediaTypeError,
    ValidationError,
)


async def _call(raw: str) -> dict[str, Any]:
    return await JSONRPCServer()._process_request(raw)


@pytest.mark.parametrize(
    ("exc", "expected_code"),
    [
        pytest.param(ConfigError("bad config"), -32602, id="config_error"),
        pytest.param(ValidationError("bad field"), -32602, id="validation_error"),
        pytest.param(PayloadTooLargeError("too big"), -32602, id="payload_too_large"),
        pytest.param(UnsupportedMediaTypeError("wrong body"), -32602, id="unsupported_media_type"),
        pytest.param(NotFoundError("no match"), -32001, id="not_found"),
        pytest.param(ConflictError("collision"), -32002, id="conflict"),
        pytest.param(DatabaseUnavailableError("no database"), -32003, id="database_unavailable"),
        pytest.param(MigrationMissingError("missing migration"), -32004, id="migration_missing"),
        pytest.param(DependencyMissingError("missing extra"), -32005, id="dependency_missing"),
        pytest.param(InternalError("server bug"), -32603, id="internal_error"),
        pytest.param(CorpusError("base failure"), -32603, id="corpus_error_base"),
    ],
)
async def test_each_taxonomy_class_answers_its_socket_code(
    monkeypatch: Any, exc: CorpusError, expected_code: int
) -> None:
    """Each taxonomy class rides its own code, not the -32603 catch."""

    async def fail(method: str, params: dict[str, Any]) -> dict[str, Any]:
        raise exc

    monkeypatch.setattr(socket_ops_module, "dispatch", fail)

    raw = json.dumps({"jsonrpc": "2.0", "id": 3, "method": "any_method", "params": {}})
    response = await _call(raw)

    assert response["error"]["code"] == expected_code
    assert response["error"]["message"] == str(exc)
    assert response["id"] == 3


async def test_socket_unknown_method_stays_32601() -> None:
    """A method with no arm answers -32601, never -32603."""
    raw = json.dumps({"jsonrpc": "2.0", "id": 4, "method": "no_such_method", "params": {}})
    response = await _call(raw)

    assert response["error"]["code"] == -32601


async def test_socket_missing_method_field_stays_32601() -> None:
    """A request with no method field answers -32601."""
    response = await _call(json.dumps({"jsonrpc": "2.0", "id": 4}))

    assert response["error"]["code"] == -32601


async def test_socket_bad_json_stays_32600() -> None:
    """A parse failure answers -32600 with a null id."""
    response = await _call("{not json")

    assert response["error"]["code"] == -32600
    assert response["id"] is None


async def test_socket_success_envelope_is_unchanged(monkeypatch: Any) -> None:
    """A success envelope keeps the exact master shape."""

    async def ok(method: str, params: dict[str, Any]) -> dict[str, Any]:
        return {"status": "success"}

    monkeypatch.setattr(socket_ops_module, "dispatch", ok)

    raw = json.dumps({"jsonrpc": "2.0", "id": 9, "method": "any_method", "params": {}})
    response = await _call(raw)

    assert response == {"jsonrpc": "2.0", "id": 9, "result": {"status": "success"}}


async def test_socket_non_taxonomy_error_stays_32603(monkeypatch: Any) -> None:
    """A plain exception is not a taxonomy class. It stays -32603."""

    async def crash(method: str, params: dict[str, Any]) -> dict[str, Any]:
        raise KeyError("query")

    monkeypatch.setattr(socket_ops_module, "dispatch", crash)

    raw = json.dumps({"jsonrpc": "2.0", "id": 5, "method": "search", "params": {}})
    response = await _call(raw)

    assert response["error"]["code"] == -32603
