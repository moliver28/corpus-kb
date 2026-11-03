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
from typing import Any, cast
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


def test_duckdb_exception_names_stay_mapped() -> None:
    """The DuckDB store/gateway error surface speaks the taxonomy per class.

    Lock and catalog failures translate to typed errors at the store
    boundary (``duckdb_store._translate_error``); these raw-class rows pin
    the honest triple when a name still escapes through a legacy envelope.
    """
    # Contention and environment: the database-unavailable triple.
    for name in ("IOException", "ConnectionException", "TransactionException"):
        assert ERROR_MAP[name] == (503, -32003, 3), name
    # Missing schema objects: the migration-missing triple.
    assert ERROR_MAP["CatalogException"] == (503, -32004, 3)
    # Bad caller input: the invalid-params triple.
    for name in ("InvalidInputException", "ConversionException", "ParserException"):
        assert ERROR_MAP[name] == (400, -32602, 1), name
    # A constraint collision is a conflict, not a crash.
    assert ERROR_MAP["ConstraintException"] == (409, -32002, 3)
    # A feature the engine lacks: the dependency-missing triple.
    assert ERROR_MAP["NotImplementedException"] == (503, -32005, 3)


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


# ---------------------------------------------------------------------------
# Wave-1 conversions (issue #229) speak the taxonomy at the raise site
#
# One failure class = one HTTP status + one socket code + one CLI exit.
# Each pin names the file whose raise moved to a typed class and asserts
# the exact wire triple off the raised instance. Messages stay unchanged;
# the disclosed rename is the ``error_type`` string only (for example
# "ValueError" -> "validation_error").
# ---------------------------------------------------------------------------


def _assert_triple(exc: CorpusError, http: int, socket_code: int, cli: int) -> None:
    assert (exc.http_status, exc.socket_code, exc.cli_exit) == (http, socket_code, cli)


async def test_search_similar_rejects_a_malformed_chunk_id() -> None:
    from corpus_kb.tools import search_tools

    with pytest.raises(ValidationError, match="is not a valid UUID") as excinfo:
        await search_tools.search_similar(object(), chunk_id="not-a-uuid")
    _assert_triple(excinfo.value, 400, -32602, 1)


async def test_get_document_tags_rejects_a_malformed_doc_id() -> None:
    import corpus_kb.tools.tag_tools as tag_tools

    with pytest.raises(ValidationError, match="is not a valid UUID") as excinfo:
        await tag_tools.get_document_tags(object(), doc_id="not-a-uuid")
    _assert_triple(excinfo.value, 400, -32602, 1)


async def test_get_metadata_rejects_a_malformed_doc_id() -> None:
    import corpus_kb.tools.tag_tools as tag_tools

    with pytest.raises(ValidationError, match="is not a valid UUID") as excinfo:
        await tag_tools.get_metadata(object(), doc_id="not-a-uuid")
    _assert_triple(excinfo.value, 400, -32602, 1)


async def test_sweep_engine_refuses_use_before_open() -> None:
    from corpus_kb.tools.pipeline.calibrate_sweep_engine import ScratchTenantEngine

    engine = ScratchTenantEngine({}, uuid4(), 0.5)
    with pytest.raises(InternalError, match="used before open") as excinfo:
        engine._pool_or_raise()
    _assert_triple(excinfo.value, 500, -32603, 1)


def test_migrate_attach_refuses_a_dsn_with_quotes() -> None:
    from corpus_kb.handlers.migrate_attach import attach_source

    class _Conn:
        def execute(self, _sql: str) -> None:
            return None

    with pytest.raises(ValidationError, match="quotes or NUL") as excinfo:
        attach_source(_Conn(), "postgres://user'pass@host/db")
    _assert_triple(excinfo.value, 400, -32602, 1)


async def test_run_migration_rejects_an_unknown_direction() -> None:
    from corpus_kb.handlers.migrate_roundtrip import run_migration

    with pytest.raises(ValidationError, match="Unknown migration direction") as excinfo:
        await run_migration("sideways", "postgres://stub", "duckdb://stub")
    _assert_triple(excinfo.value, 400, -32602, 1)


async def test_unknown_embedding_provider_raises_config_error() -> None:
    from corpus_kb.domain.models import SearchQuery
    from corpus_kb.handlers.query_search_pg import PostgresSearchBackend

    backend = PostgresSearchBackend(embedder=None, config={"embedding": {"provider": "bogus"}})
    query = SearchQuery(tenant_id=uuid4(), query="q")
    with pytest.raises(ConfigError, match="Unknown embedding provider") as excinfo:
        await backend._vector_arm_rows(object(), query, "q", [], "", 10, [])
    _assert_triple(excinfo.value, 400, -32602, 3)


async def test_release_execute_rejects_a_malformed_waiver_pair() -> None:
    from corpus_kb.tools import release_tools

    with pytest.raises(ValidationError, match="waiver must be") as excinfo:
        await release_tools.release_execute(object(), "x", "y", "dana", waivers=[["only"]])
    _assert_triple(excinfo.value, 400, -32602, 1)


async def test_ingest_transcript_missing_file_raises_not_found(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import corpus_kb.handlers.research_handler as research_handler_module

    monkeypatch.setattr(research_handler_module, "get_app", lambda *_a: object())
    handler = research_handler_module.ResearchHandler(pool=object())
    with pytest.raises(NotFoundError, match="no-such-transcript") as excinfo:
        await handler.ingest_transcript(uuid4(), "no-such-transcript.txt")
    _assert_triple(excinfo.value, 404, -32001, 1)


class _RepoStub:
    def __init__(self, value: object) -> None:
        self._value = value

    def get(self, _aggregate_id: object) -> object:
        return self._value


class _AppStub:
    def __init__(self, value: object) -> None:
        self.repository = _RepoStub(value)

    def save(self, _aggregate: object) -> None:
        return None


async def test_review_assignment_wrong_aggregate_type_raises_validation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import corpus_kb.handlers.research_handler as research_handler_module

    monkeypatch.setattr(research_handler_module, "get_app", lambda *_a: _AppStub(object()))
    handler = research_handler_module.ResearchHandler(pool=object())
    with pytest.raises(ValidationError, match="is not a CodingAssignment") as excinfo:
        handler.review_assignment(uuid4(), uuid4(), "dana", "accept")
    _assert_triple(excinfo.value, 400, -32602, 1)


async def test_set_keywords_wrong_aggregate_type_raises_validation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import corpus_kb.handlers.research_handler as research_handler_module

    monkeypatch.setattr(research_handler_module, "get_app", lambda *_a: _AppStub(object()))
    handler = research_handler_module.ResearchHandler(pool=object())
    with pytest.raises(ValidationError, match="is not a CodebookVersion") as excinfo:
        handler.set_keywords(uuid4(), uuid4(), uuid4(), [{"word": "x"}])
    _assert_triple(excinfo.value, 400, -32602, 1)


async def test_load_run_wrong_aggregate_type_raises_validation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import corpus_kb.handlers.research_handler as research_handler_module

    monkeypatch.setattr(research_handler_module, "get_app", lambda *_a: _AppStub(object()))
    handler = research_handler_module.ResearchHandler(pool=object())
    with pytest.raises(ValidationError, match="is not a CodingRun") as excinfo:
        handler._load_run(uuid4())
    _assert_triple(excinfo.value, 400, -32602, 1)


def test_command_handler_requires_a_pool_for_ingest() -> None:
    from corpus_kb.handlers.command_handler import CommandHandler

    handler = CommandHandler(pool=None, gateway=None)
    with pytest.raises(InternalError, match="requires an asyncpg") as excinfo:
        handler._require_pool()
    _assert_triple(excinfo.value, 500, -32603, 1)


async def test_command_handler_delete_needs_a_pool_or_gateway() -> None:
    from corpus_kb.domain.models import DeleteDocumentCommand
    from corpus_kb.handlers.command_handler import CommandHandler

    handler = CommandHandler(pool=None, gateway=None)
    with pytest.raises(InternalError, match="pool or a gateway") as excinfo:
        await handler.handle_delete_document(DeleteDocumentCommand(doc_id=uuid4()))
    _assert_triple(excinfo.value, 500, -32603, 1)


async def test_tag_handler_missing_row_after_insert_raises_internal_error() -> None:
    import corpus_kb.handlers.tag_handler as tag_handler_module

    class _Reader:
        async def fetchrow(self, *_args: object) -> None:
            return None

        async def execute(self, *_args: object) -> str:
            return "OK"

    handler = tag_handler_module.TagHandler(object())
    with pytest.raises(InternalError, match="Tag row missing") as excinfo:
        await handler._tag_id(_Reader(), ("tag", "tenant"))
    _assert_triple(excinfo.value, 500, -32603, 1)


# ---------------------------------------------------------------------------
# Wave-2 conversions (issue #229) speak the taxonomy at the raise site
#
# Scope: ingestion/, storage/, rag/, web/ plus the bare-UUID residue in
# ask/cycle/graph/graph_read/research tools. Same wave-1 rule: convert only
# when the typed class keeps the site's effective wire triple; the disclosed
# change is the ``error_type`` string (plus the three config-vocabulary sites
# that move to ConfigError's documented CLI exit 3, named below).
# ---------------------------------------------------------------------------


def test_domain_error_rebases_speak_the_taxonomy() -> None:
    """Builtin-based domain classes rebase onto the taxonomy (issue #229).

    Each base keeps the class's effective wire triple, so every raise site
    behind the class moves to the taxonomy with zero envelope change:
    ValueError-based -> ValidationError (400/-32602/1), Exception- and
    RuntimeError-based -> InternalError (500/-32603/1, the unmapped
    default these classes already produced).
    """
    from corpus_kb.ingestion.registry import UnknownStrategyError
    from corpus_kb.ingestion.strategies.textbook_docling import _MalformedRowError
    from corpus_kb.rag.embedders._latechunk_types import LateChunkDependencyError
    from corpus_kb.storage.age_graph_store import AgeUnavailableError
    from corpus_kb.storage.llamaindex_backend import DimensionMismatchError
    from corpus_kb.web.fetch import WebFetchError

    assert issubclass(UnknownStrategyError, ValidationError)
    assert issubclass(DimensionMismatchError, ValidationError)
    assert issubclass(_MalformedRowError, InternalError)
    assert issubclass(AgeUnavailableError, InternalError)
    assert issubclass(LateChunkDependencyError, InternalError)
    assert issubclass(WebFetchError, InternalError)
    # Instance triples ride the class attributes.
    _assert_triple(UnknownStrategyError("x"), 400, -32602, 1)
    _assert_triple(WebFetchError("x"), 500, -32603, 1)


async def test_get_strategy_unknown_name_raises_validation_error() -> None:
    from corpus_kb.ingestion.registry import get_strategy

    with pytest.raises(ValidationError, match="Valid strategies") as excinfo:
        get_strategy("not-a-strategy")
    _assert_triple(excinfo.value, 400, -32602, 1)


def test_register_strategy_rejects_a_nameless_strategy() -> None:
    from corpus_kb.ingestion.base import IngestionStrategy
    from corpus_kb.ingestion.registry import register_strategy

    class _NoName:
        name = ""

    with pytest.raises(ValidationError, match="non-empty string") as excinfo:
        register_strategy(cast(IngestionStrategy, _NoName()))
    _assert_triple(excinfo.value, 400, -32602, 1)


def test_register_strategy_rejects_an_incomplete_strategy() -> None:
    from corpus_kb.ingestion.base import IngestionStrategy
    from corpus_kb.ingestion.registry import register_strategy

    class _Half:
        name = "half"

    with pytest.raises(ValidationError, match="detect") as excinfo:
        register_strategy(cast(IngestionStrategy, _Half()))
    _assert_triple(excinfo.value, 400, -32602, 1)


async def test_create_graph_store_rejects_an_unknown_graph_backend() -> None:
    from corpus_kb.storage.graph_factory import create_graph_store

    with pytest.raises(ConfigError, match="Unknown graph backend") as excinfo:
        await create_graph_store(
            {"database": {"backend": "postgres"}, "graph": {"backend": "bogus"}}
        )
    _assert_triple(excinfo.value, 400, -32602, 3)


async def test_create_graph_store_rejects_an_unknown_database_backend() -> None:
    from corpus_kb.storage.graph_factory import create_graph_store

    with pytest.raises(ConfigError, match="Unknown database backend") as excinfo:
        await create_graph_store({"database": {"backend": "sqlite"}})
    _assert_triple(excinfo.value, 400, -32602, 3)


def test_duckdb_conn_string_rejects_an_unsupported_scheme() -> None:
    from corpus_kb.storage.duckdb_support import duckdb_conn_string_to_path

    with pytest.raises(ValidationError, match="Unsupported connection string") as excinfo:
        duckdb_conn_string_to_path("mysql://host/db")
    _assert_triple(excinfo.value, 400, -32602, 1)


def test_duckpgq_traversal_rejects_an_unknown_direction() -> None:
    from corpus_kb.storage.duckpgq import traversal_statements

    with pytest.raises(ValidationError, match="direction must be one of") as excinfo:
        traversal_statements("sideways", 2, "tenant", "entity")
    _assert_triple(excinfo.value, 400, -32602, 1)


def test_graph_sql_validators_raise_validation_error() -> None:
    from corpus_kb.storage.graph_sql import validate_depth, validate_hops

    with pytest.raises(ValidationError, match="hops must be between 1"):
        validate_hops(0)
    with pytest.raises(ValidationError, match="max_depth must be 1 or more"):
        validate_depth(0)


async def test_age_neighbors_reject_an_unknown_direction() -> None:
    from corpus_kb.storage.age_graph_store import AgeGraphStore

    store = AgeGraphStore(cast(Any, object()))
    with pytest.raises(ValidationError, match="direction must be one of") as excinfo:
        await store.neighbors("eid", hops=1, direction="sideways")
    _assert_triple(excinfo.value, 400, -32602, 1)


async def test_llamaindex_backend_requires_initialize() -> None:
    from corpus_kb.storage.llamaindex_backend import LlamaIndexPostgresBackend

    backend = LlamaIndexPostgresBackend({})
    with pytest.raises(InternalError, match="not initialized") as excinfo:
        await backend.ingest("source-id", [])
    _assert_triple(excinfo.value, 500, -32603, 1)


def test_create_embedder_rejects_an_unknown_provider() -> None:
    from corpus_kb.rag.embedder import create_embedder

    with pytest.raises(ConfigError, match="Unknown embedding provider") as excinfo:
        create_embedder({"embedding": {"provider": "bogus"}}, pool=None)
    _assert_triple(excinfo.value, 400, -32602, 3)


def test_profile_for_model_rejects_an_unknown_model() -> None:
    from corpus_kb.rag.model_profiles import profile_for_model

    with pytest.raises(ConfigError, match="no EmbeddingModelProfile") as excinfo:
        profile_for_model("no-such-model")
    _assert_triple(excinfo.value, 400, -32602, 3)


def test_rerank_settings_reject_a_non_bool_enabled() -> None:
    from corpus_kb.rag.rerank_settings import load_rerank_settings

    with pytest.raises(ValidationError, match=r"reranking.enabled must be a bool") as excinfo:
        load_rerank_settings({"reranking": {"enabled": "yes"}})
    _assert_triple(excinfo.value, 400, -32602, 1)


# ---------------------------------------------------------------------------
# Wave-2 bare-UUID wraps: tool caller ids go through uuid_arg -> ValidationError
# ---------------------------------------------------------------------------


async def _bad_tenant(call: Any) -> None:
    with pytest.raises(ValidationError, match="is not a valid UUID") as excinfo:
        await call()
    _assert_triple(excinfo.value, 400, -32602, 1)


async def test_ask_rejects_a_malformed_tenant_id() -> None:
    from corpus_kb.tools import ask_tools

    await _bad_tenant(lambda: ask_tools.ask("q", tenant_id="not-a-uuid"))


async def test_research_cycle_rejects_malformed_ids() -> None:
    from corpus_kb.tools import cycle_tools

    await _bad_tenant(lambda: cycle_tools.research_cycle(object(), tenant_id="not-a-uuid"))
    await _bad_tenant(lambda: cycle_tools.research_cycle(object(), project_id="not-a-uuid"))


async def test_graph_tools_reject_malformed_ids() -> None:
    from corpus_kb.tools import graph_tools

    await _bad_tenant(lambda: graph_tools.add_entity(object(), "n", tenant_id="not-a-uuid"))
    await _bad_tenant(
        lambda: graph_tools.add_relation(
            object(), source_entity_id="not-a-uuid", target_entity_id=str(uuid4())
        )
    )


async def test_graph_read_tools_reject_malformed_ids() -> None:
    from corpus_kb.tools import graph_read_tools

    # The id guard fires BEFORE the graph handler resolves, so no database
    # or singleton wiring is needed to see the typed rejection.
    await _bad_tenant(lambda: graph_read_tools.search_graph(object(), "q", tenant_id="not-a-uuid"))
    await _bad_tenant(lambda: graph_read_tools.get_entity_relations(object(), "not-a-uuid"))
    await _bad_tenant(
        lambda: graph_read_tools.graph_neighbors(object(), "e", tenant_id="not-a-uuid")
    )
    await _bad_tenant(
        lambda: graph_read_tools.graph_traverse(object(), "e", tenant_id="not-a-uuid")
    )


async def test_research_tools_reject_malformed_ids() -> None:
    from corpus_kb.tools import research_tools

    await _bad_tenant(
        lambda: research_tools.research_ingest_transcript("f.md", object(), tenant_id="not-a-uuid")
    )
    await _bad_tenant(
        lambda: research_tools.research_ingest("f.md", object(), tenant_id="not-a-uuid")
    )
    await _bad_tenant(
        lambda: research_tools.coding_run(object(), str(uuid4()), tenant_id="not-a-uuid")
    )
    await _bad_tenant(lambda: research_tools.inductive_run(object(), tenant_id="not-a-uuid"))
    await _bad_tenant(
        lambda: research_tools.codebook_promote(object(), 1, "n", "d", tenant_id="not-a-uuid")
    )
    await _bad_tenant(lambda: research_tools.research_report(object(), tenant_id="not-a-uuid"))
    await _bad_tenant(
        lambda: research_tools.review_execute(
            object(), str(uuid4()), "accept", "r", tenant_id="not-a-uuid"
        )
    )
