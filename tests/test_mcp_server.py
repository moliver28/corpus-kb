"""Wave 4A: the MCP stdio server is real and registry-generated.

The gate proves four things, all offline (stub pool, no Ollama, no
subprocess, no stdio):

  1. The server builds, and every catalogue MCP surface plus every
     ``surface_registry`` MCP row is a registered tool — the EXCLUDE set
     stays empty or carries a reason.
  2. The advertised parameter schema comes from the shared function's
     real signature (catalogue ParamSpec names, honest defaults).
  3. In-memory client round-trips return the shared envelope for
     representative ops across families (ingest, search, documents,
     release, research).
  4. Both transport guards (``corpus-kb --transport stdio`` and
     ``server_wiring --transport stdio``) select ``run_stdio_server``;
     the HTTP path stays byte-identical.
"""

from __future__ import annotations

import json
from typing import Any
from uuid import UUID

import pytest
from mcp.client._memory import InMemoryTransport
from mcp.client.session import ClientSession
from mcp.server.mcpserver import MCPServer

import corpus_kb.handlers.query_handler as query_handler_module
import corpus_kb.handlers.release_handlers as release_handlers_module
import corpus_kb.tools.ingest_common as ingest_common_module
import corpus_kb.tools.notebook_tools as notebook_tools_module
import corpus_kb.tools.research_tools as research_tools_module
from corpus_kb.contracts import OPERATIONS
from corpus_kb.contracts.model import MCP, surfaces_of
from corpus_kb.mcp_server import build_mcp_server, run_stdio_server
from corpus_kb.mcp_tools import EXCLUDED_TOOLS, build_wrapper, resolve_function
from corpus_kb.surface_registry import SURFACES

CV = "11111111-1111-1111-1111-111111111111"
PROJECT_ID = "44444444-4444-4444-4444-444444444444"


class _StubConn:
    async def fetchval(self, sql: str, *args: Any) -> Any:
        if "pg_try_advisory_lock" in sql:
            return True
        if "to_regclass" in sql:
            return "public.ingest_runs"
        if "information_schema.columns" in sql:
            return 2
        return 0

    async def execute(self, sql: str, *args: Any) -> str:
        return "OK"

    async def fetch(self, sql: str, *args: Any) -> list[Any]:
        return []

    def transaction(self) -> _StubConn:
        return self

    async def __aenter__(self) -> _StubConn:
        return self

    async def __aexit__(self, *args: Any) -> bool:
        return False


class _StubPool:
    def __init__(self) -> None:
        self._conn = _StubConn()

    def acquire(self) -> _StubPool:
        return self

    async def __aenter__(self) -> Any:
        return self._conn

    async def __aexit__(self, *args: Any) -> bool:
        return False

    async def close(self) -> None:
        return None


def _offline_config() -> dict[str, Any]:
    return {
        "graph": {"extract_entities": False},
        "embedding": {"provider": "pgml"},
        "ingest": {"chunking": {"enabled": False}},
    }


def _make_server() -> MCPServer:
    return build_mcp_server(_StubPool(), _offline_config())


async def _call(server: MCPServer, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    transport = InMemoryTransport(server)
    async with (
        transport as (read_stream, write_stream),
        ClientSession(read_stream, write_stream) as session,
    ):
        await session.initialize()
        result = await session.call_tool(name, arguments)
    assert not result.is_error, f"{name}: {result.content[0].text}"
    return json.loads(result.content[0].text)


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------


async def test_every_declared_mcp_tool_is_registered() -> None:
    """Catalogue MCP surfaces + registry MCP rows == the registered set."""
    server = _make_server()
    registered = {tool.name for tool in await server.list_tools()}
    catalogue_mcp = {s.route for op in OPERATIONS for s in surfaces_of(op, MCP)}
    registry_mcp = {s.mcp_tool for s in SURFACES if s.mcp_tool}
    assert registered == catalogue_mcp | registry_mcp
    # The historical lie (declared names with no function) stays dead.
    # The §5.1 graph births (+4), the analysis program's tools (+4), and
    # the method register program's twelve method ops (+12), the Living
    # Corpus statement (+2), conflict (+3), belief (+4), tune (+1) and
    # meta-embed (+1) ops all extended the catalogue; the pin covers the
    # union.
    assert len(registered) == len(catalogue_mcp | registry_mcp) == 70


async def test_exclude_set_stays_empty_or_reasoned() -> None:
    """A tool may be skipped only with an honest recorded reason."""
    assert isinstance(EXCLUDED_TOOLS, dict)
    server = _make_server()
    registered = {tool.name for tool in await server.list_tools()}
    for name, reason in EXCLUDED_TOOLS.items():
        assert isinstance(reason, str) and reason, f"EXCLUDED_TOOLS[{name!r}] needs a reason"
        # A skipped catalogue surface must not be registered.
        assert name not in registered


def test_registry_row_without_function_fails_the_build() -> None:
    """Every registry MCP name outside the catalogue has a function mapping
    (catalogue-covered rows register through the catalogue leg)."""
    from corpus_kb.mcp_tools import REGISTRY_TOOL_FUNCTIONS

    catalogue_mcp = {s.route for op in OPERATIONS for s in surfaces_of(op, MCP)}
    for surface in SURFACES:
        if surface.mcp_tool and surface.mcp_tool not in catalogue_mcp:
            assert surface.mcp_tool in REGISTRY_TOOL_FUNCTIONS, surface.name


def test_handler_override_targets_resolve() -> None:
    from corpus_kb.mcp_tools import TOOL_LAYER_OVERRIDES

    for spec in TOOL_LAYER_OVERRIDES.values():
        assert callable(resolve_function(spec))


def test_catalogue_param_drift_fails_the_build() -> None:
    """A catalogue parameter the function does not take fails loudly."""
    from corpus_kb.contracts import get_operation

    op = get_operation("search")
    assert op is not None

    async def narrower(pg_pool: Any, query: str, k: int = 10) -> dict[str, object]:
        return {}

    with pytest.raises(ValueError, match="no_such_param"):
        build_wrapper(narrower, None, None, ("query", "k", "no_such_param"), "search")


# ---------------------------------------------------------------------------
# Schema + round-trips
# ---------------------------------------------------------------------------


async def test_search_schema_matches_catalogue_params() -> None:
    server = _make_server()
    tools = {tool.name: tool for tool in await server.list_tools()}
    schema = tools["search"].input_schema
    assert set(schema["properties"]) == {"query", "k", "source_type", "self_query", "tenant_id"}
    assert schema["required"] == ["query"]
    # W3: the catalogue default k mirrors reranking.return_top_k (20).
    assert schema["properties"]["k"]["default"] == 20


async def test_round_trip_ingest_text(monkeypatch: pytest.MonkeyPatch, tmp_path: Any) -> None:
    class _StubStore:
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

    monkeypatch.setattr(ingest_common_module, "PostgresIngestStore", _StubStore)
    payload = await _call(
        _make_server(), "ingest_text", {"text": "hello " * 4, "source": "mcp-test"}
    )
    assert payload["status"] == "success"
    assert payload["document_id"] == "doc-1"


async def test_round_trip_search(monkeypatch: pytest.MonkeyPatch) -> None:
    from corpus_kb.handlers.query_integrity import DegradedExcluded, SearchEnvelope

    async def canned_search(self: Any, query: Any) -> SearchEnvelope:
        return SearchEnvelope(status="success", results=[], degraded_excluded=DegradedExcluded())

    monkeypatch.setattr(query_handler_module.QueryHandler, "handle_search_with_meta", canned_search)
    payload = await _call(_make_server(), "search", {"query": "probe", "k": 3})
    assert set(payload) == {
        "status",
        "result",
        "degraded_excluded",
        "vector_index_mode",
        "lexical_index_mode",
        "meta_hits",
    }
    assert payload["status"] == "success"


async def test_round_trip_list_documents(monkeypatch: pytest.MonkeyPatch) -> None:
    async def canned_list_documents(self: Any, query: Any) -> list[Any]:
        return []

    monkeypatch.setattr(
        query_handler_module.QueryHandler, "handle_list_documents", canned_list_documents
    )
    payload = await _call(_make_server(), "list_documents", {"limit": 5, "offset": 0})
    assert payload == {"status": "success", "result": []}


async def test_round_trip_delete_document() -> None:
    payload = await _call(
        _make_server(), "delete_document", {"document_id": "00000000-0000-0000-0000-00000000000d"}
    )
    assert payload == {
        "status": "success",
        "document_id": "00000000-0000-0000-0000-00000000000d",
    }


async def test_round_trip_release_status(monkeypatch: pytest.MonkeyPatch) -> None:
    class _Result:
        def to_payload(self) -> dict[str, Any]:
            return {
                "status": "ok",
                "codebook_version_id": CV,
                "current_stage": "assess",
                "release_state": "idle",
                "blocking_criteria": [],
                "recommended_next_action": "release assess",
                "active_release": None,
                "draft": None,
                "pending_change_proposals": [],
                "data_sufficiency": {},
            }

    async def stub(*args: Any, **kwargs: Any) -> _Result:
        return _Result()

    monkeypatch.setattr(release_handlers_module, "status_report", stub)
    payload = await _call(_make_server(), "release_status", {"codebook_version_id": CV})
    assert payload["status"] == "ok"
    assert payload["current_stage"] == "assess"


async def test_round_trip_research_transcript(monkeypatch: pytest.MonkeyPatch) -> None:
    class _Handler:
        async def ingest_transcript(
            self,
            tenant_id: UUID,
            file_path: str,
            project_id: UUID | None = None,
            title: str | None = None,
            force: bool = False,
        ) -> dict[str, Any]:
            return {"status": "success", "doc_id": "doc-1", "n_turns": 2}

    async def noop_advance(pool: Any, tenant_id: UUID) -> None:
        return None

    monkeypatch.setattr(research_tools_module, "get_research_handler", lambda pool: _Handler())
    monkeypatch.setattr(research_tools_module, "advance_all_projections", noop_advance)
    payload = await _call(_make_server(), "research_ingest_transcript", {"file_path": "t.txt"})
    assert payload["status"] == "success"
    assert payload["doc_id"] == "doc-1"


async def test_notebook_ask_round_trip(monkeypatch: pytest.MonkeyPatch) -> None:
    async def canned_ask(pool: Any, embedder: Any, llm: Any, query: Any) -> dict[str, Any]:
        return {"status": "answer", "sentences": ["s."], "llm_calls": 0}

    monkeypatch.setattr(notebook_tools_module, "notebook_ask", canned_ask)
    payload = await _call(
        _make_server(),
        "notebook_ask",
        {"question": "q?", "project_id": PROJECT_ID, "retrieval_only": True},
    )
    assert payload["status"] == "answer"


# ---------------------------------------------------------------------------
# Full-surface parity wave: one call-through per new tool
# ---------------------------------------------------------------------------

ENTITY_A = "55555555-5555-5555-5555-555555555555"
ENTITY_B = "66666666-6666-6666-6666-666666666666"
ASSIGNMENT_ID = "77777777-7777-7777-7777-777777777777"
_REQUIRED_REPORT_KEYS = (
    "isr_pooled",
    "isr_by_source_type",
    "run_stop",
    "coverage_explicit",
    "coverage_qdep",
    "gray_zone_share",
    "tier3_disagreement_rate",
    "link_accuracy",
    "review_backlog",
    "kappa_alpha",
    "residual",
    "coverage_curve",
    "missing_codes",
    "cluster_stability",
    "overlap",
    "keywords",
    "g3_audit",
    "conformal",
    "run_manifest",
    "codebook_diff",
)


async def _canned(payload: dict[str, Any], *args: Any, **kwargs: Any) -> dict[str, Any]:
    return payload


async def test_round_trip_query(monkeypatch: pytest.MonkeyPatch) -> None:
    from corpus_kb.domain.models import RouteDecision, RoutedResult
    from corpus_kb.handlers import router_handler as router_module

    class _StubRouter:
        async def handle_routed_query(self, query: Any) -> RoutedResult:
            return RoutedResult(
                decision=RouteDecision(route="semantic", confidence=0.9, fallback=False),
                results=[],
            )

    monkeypatch.setattr(router_module, "get_router_handler", lambda: _StubRouter())
    payload = await _call(_make_server(), "query", {"query": "probe", "k": 3})
    assert payload == {
        "status": "success",
        "result": {
            "decision": {"route": "semantic", "confidence": 0.9, "fallback": False},
            "results": [],
        },
    }


async def test_round_trip_verify_answer(monkeypatch: pytest.MonkeyPatch) -> None:
    from corpus_kb.domain.models import VerifyAnswerResult

    async def canned_verify(self: Any, query: Any) -> VerifyAnswerResult:
        return VerifyAnswerResult(verdicts=[], groundedness=1.0, abstained=True)

    monkeypatch.setattr(query_handler_module.QueryHandler, "handle_verify_answer", canned_verify)
    payload = await _call(_make_server(), "verify_answer", {"answer": "a", "chunk_ids": []})
    assert payload["status"] == "success"
    assert payload["result"]["abstained"] is True


async def test_round_trip_add_entity(monkeypatch: pytest.MonkeyPatch) -> None:
    import corpus_kb.handlers.command_handler as command_handler_module

    class _Saved:
        def save(self, aggregate: Any) -> None:
            return None

    monkeypatch.setattr(command_handler_module, "get_app_for_config", lambda _cfg: _Saved())
    payload = await _call(_make_server(), "add_entity", {"name": "probe", "entity_type": "concept"})
    assert payload["status"] == "success"
    assert payload["name"] == "probe"
    assert payload["entity_id"]


async def test_round_trip_research_ingest(monkeypatch: pytest.MonkeyPatch) -> None:
    class _Handler:
        async def ingest_path(
            self, tenant_id: UUID, path: str, project_id: Any = None, force: bool = False
        ) -> dict[str, Any]:
            return {"status": "success", "files_found": 1, "ingested": 1}

    async def noop_advance(pool: Any, tenant_id: UUID) -> None:
        return None

    monkeypatch.setattr(research_tools_module, "get_research_handler", lambda pool: _Handler())
    monkeypatch.setattr(research_tools_module, "advance_all_projections", noop_advance)
    payload = await _call(_make_server(), "research_ingest", {"path": "corpus/"})
    assert payload["status"] == "success"
    assert payload["files_found"] == 1


async def test_round_trip_coding_run(monkeypatch: pytest.MonkeyPatch) -> None:
    import corpus_kb.research.deductive_run as deductive_run_module

    async def canned(*args: Any, **kwargs: Any) -> dict[str, Any]:
        return {"status": "success", "run_id": "run-1", "n_units": 3}

    async def noop_advance(pool: Any, tenant_id: UUID) -> None:
        return None

    monkeypatch.setattr(deductive_run_module, "run_deductive", canned)
    monkeypatch.setattr(research_tools_module, "advance_all_projections", noop_advance)
    payload = await _call(_make_server(), "coding_run", {"codebook_version_id": CV})
    assert payload["status"] == "success"


async def test_round_trip_inductive_run(monkeypatch: pytest.MonkeyPatch) -> None:
    import corpus_kb.config as config_module
    import corpus_kb.handlers.llm_handler as llm_handler_module
    import corpus_kb.rag as rag_module
    import corpus_kb.research.inductive_run as inductive_module

    async def canned(*args: Any, **kwargs: Any) -> dict[str, Any]:
        return {"status": "success", "run_id": "run-1", "n_units": 2}

    async def noop_advance(pool: Any, tenant_id: UUID) -> None:
        return None

    monkeypatch.setattr(config_module, "load_config", lambda: {"embedding": {"provider": "pgml"}})
    monkeypatch.setattr(inductive_module, "run_inductive", canned)
    monkeypatch.setattr(rag_module, "create_embedder", lambda cfg, pool: None)
    monkeypatch.setattr(llm_handler_module, "LlmHandler", lambda cfg: object())
    monkeypatch.setattr(research_tools_module, "advance_all_projections", noop_advance)
    payload = await _call(_make_server(), "inductive_run", {})
    assert payload["status"] == "success"


async def test_round_trip_codebook_promote(monkeypatch: pytest.MonkeyPatch) -> None:
    import corpus_kb.config as config_module
    import corpus_kb.research.promote_code as promote_module

    async def canned(*args: Any, **kwargs: Any) -> dict[str, Any]:
        return {"status": "promoted", "proposal_id": 1, "tau_dup": 0.8}

    async def noop_advance(pool: Any, tenant_id: UUID) -> None:
        return None

    monkeypatch.setattr(config_module, "load_config", lambda: {})
    monkeypatch.setattr(promote_module, "promote_proposal", canned)
    monkeypatch.setattr(research_tools_module, "advance_all_projections", noop_advance)
    payload = await _call(
        _make_server(), "codebook_promote", {"proposed_id": 1, "name": "n", "definition": "d"}
    )
    assert payload["status"] == "promoted"


async def test_round_trip_research_report(monkeypatch: pytest.MonkeyPatch) -> None:
    import corpus_kb.research.governance_report as governance_module
    import corpus_kb.research.report_runner as report_runner_module

    async def canned(*args: Any, **kwargs: Any) -> object:
        return object()

    report = {"schema_version": "1.0", **{key: {} for key in _REQUIRED_REPORT_KEYS}}
    monkeypatch.setattr(report_runner_module, "build_report", canned)
    monkeypatch.setattr(governance_module, "to_dict", lambda r: dict(report))
    payload = await _call(_make_server(), "research_report", {"level": "novice"})
    assert payload["schema_version"] == "1.0"
    assert "g3_audit" in payload


async def test_round_trip_review_execute(monkeypatch: pytest.MonkeyPatch) -> None:
    import corpus_kb.config as config_module
    import corpus_kb.research.review_surface as review_module

    async def canned(*args: Any, **kwargs: Any) -> dict[str, Any]:
        return {
            "status": "success",
            "assignment_id": ASSIGNMENT_ID,
            "decision": "accept",
            "projection": {},
        }

    monkeypatch.setattr(config_module, "load_config", lambda: {})
    monkeypatch.setattr(review_module, "execute_review", canned)
    payload = await _call(
        _make_server(),
        "review_execute",
        {"assignment_id": ASSIGNMENT_ID, "decision": "accept", "reviewer": "parity"},
    )
    assert payload["status"] == "success"
    assert payload["decision"] == "accept"


async def test_round_trip_notebook_views(monkeypatch: pytest.MonkeyPatch) -> None:
    async def canned_evidence(*args: Any, **kwargs: Any) -> dict[str, Any]:
        return {"status": "ok", "code": "c", "code_id": "1", "n_units": 0, "tabs": {}}

    async def canned_uncoded(*args: Any, **kwargs: Any) -> dict[str, Any]:
        return {"status": "ok", "version_id": CV, "units": [], "n_uncoded": 0}

    async def canned_overlap(*args: Any, **kwargs: Any) -> dict[str, Any]:
        return {"status": "ok", "version_id": CV, "flagged_pairs": []}

    monkeypatch.setattr(notebook_tools_module, "evidence_for_code", canned_evidence)
    monkeypatch.setattr(notebook_tools_module, "uncoded_units", canned_uncoded)
    monkeypatch.setattr(notebook_tools_module, "overlap_view", canned_overlap)
    evidence = await _call(_make_server(), "notebook_evidence", {"code": "c"})
    assert evidence["status"] == "ok"
    uncoded = await _call(_make_server(), "notebook_uncoded", {})
    assert uncoded["n_uncoded"] == 0
    overlap = await _call(_make_server(), "notebook_overlap", {})
    assert overlap["status"] == "ok"


async def test_round_trip_research_cycle(monkeypatch: pytest.MonkeyPatch) -> None:
    import corpus_kb.config as config_module
    import corpus_kb.research.cycle as cycle_module
    import corpus_kb.research.cycle_state as cycle_state_module

    async def canned_cycle(*args: Any, **kwargs: Any) -> int:
        return 0

    async def canned_checkpoint(*args: Any, **kwargs: Any) -> dict[str, Any]:
        return {"kind": "final"}

    monkeypatch.setattr(config_module, "load_config", lambda: {})
    monkeypatch.setattr(cycle_module, "run_cycle", canned_cycle)
    monkeypatch.setattr(cycle_state_module, "last_cycle_checkpoint", canned_checkpoint)
    payload = await _call(_make_server(), "research_cycle", {"mode": "in"})
    assert payload["status"] == "completed"
    assert payload["exit_code"] == 0


async def test_round_trip_research_demo(monkeypatch: pytest.MonkeyPatch) -> None:
    import corpus_kb.research.demo as demo_module

    async def canned_demo(cfg: Any = None) -> int:
        return 0

    monkeypatch.setattr(demo_module, "run_demo", canned_demo)
    payload = await _call(_make_server(), "research_demo", {})
    assert payload["status"] == "completed"
    assert payload["exit_code"] == 0
    assert "narration" in payload


# ---------------------------------------------------------------------------
# Transport guards
# ---------------------------------------------------------------------------


def test_cli_and_server_wiring_select_run_stdio_server(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Both stdio guards route to mcp_server.run_stdio_server; HTTP untouched."""
    from typer.testing import CliRunner

    from corpus_kb import server_wiring
    from corpus_kb.cli import app

    calls: list[str] = []

    def _fake() -> int:
        calls.append("stdio")
        return 0

    monkeypatch.setattr("corpus_kb.mcp_server.run_stdio_server", _fake)

    result = CliRunner().invoke(app, ["--transport", "stdio"])
    assert result.exit_code == 0, result.output

    with pytest.raises(SystemExit) as excinfo:
        server_wiring.main(["--transport", "stdio"])
    assert excinfo.value.code == 0

    assert calls == ["stdio", "stdio"]


def test_run_stdio_server_returns_taxonomy_exit_for_config_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A config failure exits 3 with the remediation line on stderr."""
    from corpus_kb.errors import ConfigError

    async def boom() -> None:
        raise ConfigError("No database connection string.", remediation="Run corpus-kb setup.")

    monkeypatch.setattr("corpus_kb.mcp_server._serve_stdio", boom)
    assert run_stdio_server() == 3
