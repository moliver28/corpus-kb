"""Wave-2A gate: one error taxonomy, one HTTP error path.

Pin the taxonomy contract (``corpus_kb/errors.py``): every failure class
carries one HTTP status + one JSON-RPC socket code + one CLI exit code,
and the class attributes agree with ``ERROR_MAP``. Then pin the route
behavior: every HTTP failure goes through ``api._body.error_response`` —
standard envelope ``{status, error, error_type}`` everywhere, honest
status codes (validation 400, missing 404, database down 503, bugs 500),
and the verify / delete_document / ingest-reembed defects stay dead.

Fully offline: handlers are stubbed at the same seams
``tests/test_http_route_smoke.py`` uses. No database is needed.
"""

from __future__ import annotations

import inspect
import json
from typing import Any
from uuid import uuid4

import pytest
from starlette.testclient import TestClient

import corpus_kb.api.research_http as research_http
import corpus_kb.handlers.command_handler as command_handler_module
import corpus_kb.handlers.query_handler as query_handler_module
import corpus_kb.tools.ingest_surface_tools as ingest_surface_tools
from corpus_kb.api._body import error_response
from corpus_kb.api.http import create_http_app
from corpus_kb.errors import (
    ERROR_MAP,
    ConfigError,
    ConflictError,
    CorpusError,
    DatabaseUnavailableError,
    DependencyMissingError,
    InternalError,
    MigrationMissingError,
    NotFoundError,
    ValidationError,
    error_status,
)

TAXONOMY = [
    ConfigError,
    ValidationError,
    NotFoundError,
    ConflictError,
    DatabaseUnavailableError,
    MigrationMissingError,
    DependencyMissingError,
    InternalError,
]


# ---------------------------------------------------------------------------
# The mapping itself
# ---------------------------------------------------------------------------


def test_every_error_map_entry_has_three_int_codes() -> None:
    for error_type, triple in ERROR_MAP.items():
        assert isinstance(triple, tuple) and len(triple) == 3, error_type
        http_status, socket_code, cli_exit = triple
        assert isinstance(http_status, int) and 400 <= http_status <= 599, error_type
        assert isinstance(socket_code, int) and -32603 <= socket_code < 0, error_type
        assert isinstance(cli_exit, int) and 0 <= cli_exit <= 255, error_type


@pytest.mark.parametrize("cls", TAXONOMY, ids=lambda cls: cls.__name__)
def test_taxonomy_class_triple_matches_error_map(cls: type[CorpusError]) -> None:
    assert cls.error_type in ERROR_MAP, cls.error_type
    assert ERROR_MAP[cls.error_type] == (cls.http_status, cls.socket_code, cls.cli_exit)


@pytest.mark.parametrize("cls", TAXONOMY, ids=lambda cls: cls.__name__)
def test_every_class_has_one_short_actionable_remediation(cls: type[CorpusError]) -> None:
    words = cls.remediation.split()
    assert 2 <= len(words) <= 20, cls.remediation
    assert cls.remediation[0].isupper(), cls.remediation


def test_socket_codes_follow_the_documented_plan() -> None:
    # -32602 (invalid params): caller input is wrong.
    assert ValidationError.socket_code == -32602
    assert ConfigError.socket_code == -32602
    # -32603: internal error.
    assert InternalError.socket_code == -32603
    # -32000 range: implementation-defined server errors.
    for cls in (NotFoundError, ConflictError, DatabaseUnavailableError):
        assert -32099 <= cls.socket_code <= -32000, cls.__name__


def test_cli_exit_codes_keep_existing_repo_conventions() -> None:
    # cli_ingest EXIT_CONFIG=3 documents "unreachable database, migration
    # missing"; promote exits 3 on duplicate_blocked (conflict).
    assert ConfigError.cli_exit == 3
    assert DatabaseUnavailableError.cli_exit == 3
    assert MigrationMissingError.cli_exit == 3
    assert DependencyMissingError.cli_exit == 3
    assert ConflictError.cli_exit == 3
    # cli._cli_error_exit maps ValueError (unknown gate, bad UUID) to 1;
    # an uncaught traceback also exits 1.
    assert ValidationError.cli_exit == 1
    assert NotFoundError.cli_exit == 1
    assert InternalError.cli_exit == 1


def test_known_error_types_resolve_to_honest_statuses() -> None:
    assert error_status("validation_error") == 400
    assert error_status("config_error") == 400
    assert error_status("not_found") == 404
    assert error_status("conflict") == 409
    assert error_status("internal_error") == 500
    assert error_status("database_unavailable") == 503
    assert error_status("migration_missing") == 503
    assert error_status("dependency_missing") == 503


def test_legacy_exception_names_stay_mapped() -> None:
    # The strings the old ``type(exc).__name__`` envelopes carried.
    assert error_status("ValueError") == 400
    assert error_status("FileNotFoundError") == 404
    assert error_status("ConnectionError") == 503
    assert error_status("UndefinedFunctionError") == 503


@pytest.mark.parametrize("unknown", ["", "not_a_type", "RuntimeError"])
def test_unknown_error_type_defaults_to_500(unknown: str) -> None:
    assert error_status(unknown) == 500


def test_corpus_error_str_is_the_message_and_remediation_overrides() -> None:
    exc = NotFoundError("No document with that id.", remediation="Check the document id.")
    assert str(exc) == "No document with that id."
    assert exc.remediation == "Check the document id."
    assert exc.error_type == "not_found"
    assert exc.http_status == 404


# ---------------------------------------------------------------------------
# error_response: the ONE envelope builder
# ---------------------------------------------------------------------------


def _body_of(response: Any) -> dict[str, object]:
    return json.loads(response.body)


def test_error_response_from_corpus_error() -> None:
    response = error_response(ConflictError("A code with that name exists."))
    assert response.status_code == 409
    assert _body_of(response) == {
        "status": "error",
        "error": "A code with that name exists.",
        "error_type": "conflict",
    }


def test_error_response_from_mapped_exception_keeps_class_name() -> None:
    response = error_response(FileNotFoundError("nope.txt"))
    assert response.status_code == 404
    assert _body_of(response) == {
        "status": "error",
        "error": "nope.txt",
        "error_type": "FileNotFoundError",
    }


def test_error_response_from_unknown_exception_is_internal_error() -> None:
    response = error_response(RuntimeError("boom"))
    assert response.status_code == 500
    body = _body_of(response)
    assert body["status"] == "error"
    assert body["error"] == "boom"
    assert body["error_type"] == "internal_error"


def test_error_response_from_string_error_type() -> None:
    response = error_response("not_found", "No document with that id.")
    assert response.status_code == 404
    assert _body_of(response) == {
        "status": "error",
        "error": "No document with that id.",
        "error_type": "not_found",
    }


def test_error_response_from_unmapped_string_defaults_to_500() -> None:
    assert error_response("something_new").status_code == 500


# ---------------------------------------------------------------------------
# Route behavior (stubs at the smoke-test seams)
# ---------------------------------------------------------------------------


class _Raiser:
    """Handler stub whose every method raises the same exception.

    ``sync=True`` matches the sync handler methods (handle_add_entity and
    friends): the exception must fire at call time, not at await time.
    """

    def __init__(self, exc: BaseException, *, sync: bool = False) -> None:
        self._exc = exc
        self._sync = sync

    def __getattr__(self, name: str) -> Any:
        exc = self._exc
        if self._sync:

            def _raise(*args: object, **kwargs: object) -> object:
                raise exc

        else:

            async def _raise(*args: object, **kwargs: object) -> object:
                raise exc

        return _raise


def _client() -> TestClient:
    return TestClient(create_http_app())


def _assert_standard_envelope(response: Any) -> dict[str, object]:
    body = response.json()
    assert set(body) == {"status", "error", "error_type"}, body
    assert body["status"] == "error"
    assert body["error"]
    assert body["error_type"]
    return body


def test_malformed_request_is_400_with_standard_envelope() -> None:
    # UUID("not-a-uuid") raises ValueError -> mapped 400.
    response = _client().post("/api/coding/saturation", json={"tenant_id": "not-a-uuid"})
    assert response.status_code == 400
    _assert_standard_envelope(response)


def test_missing_file_maps_to_404(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    exc = FileNotFoundError("no-such-file.txt")
    monkeypatch.setattr(command_handler_module, "get_command_handler", lambda *a: _Raiser(exc))
    response = _client().post("/api/ingest/file", json={"file_path": "no-such-file.txt"})
    assert response.status_code == 404
    body = _assert_standard_envelope(response)
    assert body["error_type"] == "FileNotFoundError"


def test_not_found_error_maps_to_404(monkeypatch: pytest.MonkeyPatch) -> None:
    import corpus_kb.handlers.tag_handler as tag_handler_module

    monkeypatch.setattr(
        tag_handler_module,
        "get_tag_handler",
        lambda *a: _Raiser(NotFoundError("No document with that id.")),
    )
    response = _client().get(f"/api/documents/{uuid4()}/tags")
    assert response.status_code == 404
    body = _assert_standard_envelope(response)
    assert body["error_type"] == "not_found"


def test_database_unavailable_maps_to_503(monkeypatch: pytest.MonkeyPatch) -> None:
    exc = DatabaseUnavailableError("The database is not reachable.")
    monkeypatch.setattr(query_handler_module, "get_query_handler", lambda *a: _Raiser(exc))
    response = _client().post("/api/query/sql", json={"sql": "SELECT 1"})
    assert response.status_code == 503
    body = _assert_standard_envelope(response)
    assert body["error_type"] == "database_unavailable"


def test_connection_error_maps_to_503(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        query_handler_module,
        "get_query_handler",
        lambda *a: _Raiser(ConnectionError("connection refused")),
    )
    response = _client().post("/api/query/sql", json={"sql": "SELECT 1"})
    assert response.status_code == 503
    _assert_standard_envelope(response)


def test_unexpected_exception_maps_to_500_internal_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        query_handler_module, "get_query_handler", lambda *a: _Raiser(RuntimeError("boom"))
    )
    response = _client().post("/api/query/sql", json={"sql": "SELECT 1"})
    assert response.status_code == 500
    body = _assert_standard_envelope(response)
    assert body["error_type"] == "internal_error"


def test_verify_errors_use_the_standard_envelope(monkeypatch: pytest.MonkeyPatch) -> None:
    # Regression: verify alone returned 500 + {"status", "message"}.
    exc = ValidationError("The answer text is empty.")
    monkeypatch.setattr(query_handler_module, "get_query_handler", lambda *a: _Raiser(exc))
    response = _client().post("/api/verify", json={"answer": "", "chunk_ids": []})
    assert response.status_code == 400
    body = _assert_standard_envelope(response)
    assert body["error_type"] == "validation_error"


def test_delete_document_errors_carry_error_type(monkeypatch: pytest.MonkeyPatch) -> None:
    # Regression: delete_document omitted the error_type key. The handler is
    # async since Wave 3A (it awaits the shared tool), so the default async
    # raiser matches: the exception fires at await time.
    exc = NotFoundError("No document with that id.")
    monkeypatch.setattr(command_handler_module, "get_command_handler", lambda *a: _Raiser(exc))
    response = _client().delete(f"/api/documents/{uuid4()}")
    assert response.status_code == 404
    _assert_standard_envelope(response)


def test_ingest_reembed_unavailable_maps_to_503(monkeypatch: pytest.MonkeyPatch) -> None:
    """Regression: ingest-status said 503 on unavailable, reembed said 200."""

    async def unavailable(*_args: object, **_kwargs: object) -> dict[str, object]:
        return {
            "status": "unavailable",
            "reason": "migration 022 not applied",
            "scanned": 0,
            "fixed": 0,
            "failed": 0,
        }

    monkeypatch.setattr(ingest_surface_tools, "ingest_reembed", unavailable)
    monkeypatch.setattr(research_http, "_pool", object())
    response = _client().post("/api/ingest/reembed", json={})
    assert response.status_code == 503
    assert response.json()["status"] == "unavailable"


# ---------------------------------------------------------------------------
# research_http._respond: payload statuses become honest codes
# ---------------------------------------------------------------------------


def _async_returning(payload: dict[str, object]) -> Any:
    async def fn(**_kwargs: object) -> dict[str, object]:
        return payload

    return fn


async def test_respond_error_payload_with_error_type() -> None:
    response = await research_http._respond(
        _async_returning({"status": "error", "error": "gone", "error_type": "not_found"}), {}
    )
    assert response.status_code == 404


async def test_respond_error_payload_without_error_type_is_400() -> None:
    response = await research_http._respond(
        _async_returning({"status": "error", "missing_required_fields": ["x"]}), {}
    )
    assert response.status_code == 400


async def test_respond_blocked_stays_409() -> None:
    response = await research_http._respond(
        _async_returning({"status": "blocked", "reason": "gate"}), {}
    )
    assert response.status_code == 409


async def test_respond_unavailable_maps_to_503() -> None:
    response = await research_http._respond(
        _async_returning({"status": "unavailable", "reason": "extra missing"}), {}
    )
    assert response.status_code == 503


async def test_respond_success_stays_200() -> None:
    response = await research_http._respond(
        _async_returning({"status": "success", "result": []}), {}
    )
    assert response.status_code == 200


async def test_respond_raised_corpus_error_uses_its_status() -> None:
    async def fn(**_kwargs: object) -> dict[str, object]:
        raise MigrationMissingError("corpus.rrf_fusion() is missing.")

    response = await research_http._respond(fn, {})
    assert response.status_code == 503
    body = json.loads(response.body)
    assert body["error_type"] == "migration_missing"


# ---------------------------------------------------------------------------
# Exemplar raise sites speak the taxonomy (no live database needed)
# ---------------------------------------------------------------------------


def test_fusion_error_helper_raises_migration_missing() -> None:
    """The shared fusion failure is typed, names migration 006, gives a fix.

    The type lives in ``search_settings.missing_rrf_fusion_error`` — the one
    source both fusion call sites delegate to.
    """
    from corpus_kb.research import search_settings

    source = inspect.getsource(search_settings)
    assert "MigrationMissingError" in source
    assert "corpus.rrf_fusion()" in source
    assert "corpus-kb setup" in source


def test_fusion_call_sites_delegate_to_the_shared_helper() -> None:
    """The remaining SQL fusion call site delegates, and never restates.

    research/retrieval still calls corpus.rrf_fusion in SQL, so its missing-
    function failure must go through the shared helper. The query handler
    fuses in Python (handlers/query_fusion.py) — it carries no fusion SQL
    and therefore no fusion error path at all."""
    from corpus_kb.handlers import query_handler
    from corpus_kb.research import retrieval

    assert "missing_rrf_fusion_error(exc)" in inspect.getsource(retrieval)
    assert "MigrationMissingError" not in inspect.getsource(retrieval)
    # The handler port has neither the helper nor the SQL function call.
    assert "missing_rrf_fusion_error" not in inspect.getsource(query_handler)
    assert "FROM corpus.rrf_fusion" not in inspect.getsource(query_handler)


async def test_server_wiring_without_connection_string_raises_config_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The startup guard raises ConfigError with the actionable message."""
    from corpus_kb.server_wiring import startup

    monkeypatch.delenv("CORPUS_KB_DATABASE_URL", raising=False)
    # A truthy config with an empty database section: guards against the
    # packaged default config providing a connection string.
    with pytest.raises(ConfigError) as excinfo:
        await startup(config={"database": {}})
    assert "CORPUS_KB_DATABASE_URL" in str(excinfo.value)
    assert excinfo.value.http_status == 400


async def test_initialize_postgres_pool_failure_raises_database_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import asyncpg

    from corpus_kb.server_wiring import initialize_postgres_pool

    async def refuse(*_args: object, **_kwargs: object) -> None:
        raise ConnectionError("connection refused")

    monkeypatch.setattr(asyncpg, "create_pool", refuse)
    with pytest.raises(DatabaseUnavailableError):
        await initialize_postgres_pool("postgresql://stub")


def test_config_error_is_a_corpus_error_with_triple() -> None:
    exc = ConfigError("No database connection string.")
    assert isinstance(exc, CorpusError)
    assert (exc.http_status, exc.socket_code, exc.cli_exit) == ERROR_MAP["config_error"]
