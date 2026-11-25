"""Zero-drift flagship gate: envelope parity across shared tool / HTTP / socket / MCP.

The catalogue in ``corpus_kb.contracts`` is THE source of truth: this test
and ``corpus-kb operations`` iterate the SAME rows, so they cannot diverge.
For each catalogue operation the SAME arguments go through the legs the
catalogue declares: (a) ``shared`` — the ONE function every surface routes
through (the tool layer); (b) ``http`` — the Starlette route via TestClient
(stubbed pool); (c) ``socket`` — the JSON-RPC request-processing entry
point; (d) ``mcp`` — the registry-generated MCP server via the SDK's
in-memory transport, no stdio subprocess.

Per operation the gate asserts: (1) SUCCESS envelope key-sets are equal
across legs, minus the additive keys the catalogue declares for that
surface — an undeclared extra key on ONE surface fails with a printed
diff, and the MCP surface declares NO extras because the tool functions
ARE the envelope; and (2) the catalogue's ``envelope_keys`` are all
present on the shared leg.

Sync rules (program standard 2, registry-first): a catalogue row without
legs (or legs without a row) fails ``test_catalogue_and_legs_agree``, and
rows whose operation exists in ``surface_registry`` must byte-match its
declared columns (CLI path, MCP tool name, HTTP route). Ops that cannot
run hermetically live in the catalogue's ``SKIPPED_OPERATIONS`` with an
honest reason. One declared exemption class exists: an operation whose
catalogue row declares a REDUCED surface set (the multipart upload is
HTTP-only by nature — ruling 4a). The gates read each row's declared
surface kinds, so a missing non-declared leg is DECLARED-EXEMPT, never a
silent skip: the row itself carries the declaration.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from pathlib import Path
from typing import Any, cast
from uuid import UUID

import numpy as np
import pytest
from _parity_legs import (
    _ASK,
    _ASSESS,
    _CALIBRATE,
    _CODE_BATCH,
    _CODING,
    _COMPARE,
    _DIR_PATH,
    _EMBED_CODEBOOK,
    _EVIDENCE,
    _EXECUTE,
    _FILE_PATH,
    _GOLD_PATH,
    _INDUCTIVE,
    _INGEST_PATH,
    _OVERLAP,
    _POOL,
    _PROMOTE,
    _QUEUE_ROW,
    _RELIABILITY,
    _REPORT,
    _REQUEST,
    _REVIEW,
    _REVIEWED,
    _ROUTE_QUESTION,
    _ROUTED_RESULT,
    _SATURATION,
    _STATUS,
    _TRANSCRIPT,
    _UNCODED,
    _URL_HTML,
    _VERIFY_RESULT,
    ENTITY_A,
    ENTITY_B,
    LEGS,
    PROJECT_ID,
    TEXT_BODY,
    _shared_demo_load,
)
from starlette.testclient import TestClient

import corpus_kb.analysis.nli_contract as nli_contract_module
import corpus_kb.api.research_http as research_http_module
import corpus_kb.config as config_module
import corpus_kb.handlers.coding_handler as coding_handler_module
import corpus_kb.handlers.command_handler as command_handler_module
import corpus_kb.handlers.delete_audit as delete_audit_module
import corpus_kb.handlers.graph_handler as graph_handler_module
import corpus_kb.handlers.llm_handler as llm_handler_module
import corpus_kb.handlers.query_handler as query_handler_module
import corpus_kb.handlers.release_handlers as release_handlers_module
import corpus_kb.handlers.router_handler as router_handler_module
import corpus_kb.handlers.tag_handler as tag_handler_module
import corpus_kb.labeling.engine as labeling_engine_module
import corpus_kb.labeling.writer as labeling_writer_module
import corpus_kb.projections.research._embed as research_embed_module
import corpus_kb.rag as rag_module
import corpus_kb.research.cycle as research_cycle_module
import corpus_kb.research.cycle_state as research_cycle_state_module
import corpus_kb.research.deductive_run as deductive_run_module
import corpus_kb.research.demo as research_demo_module
import corpus_kb.research.governance_report as governance_report_module
import corpus_kb.research.inductive_run as inductive_run_module
import corpus_kb.research.promote_code as promote_code_module
import corpus_kb.research.report_runner as report_runner_module
import corpus_kb.research.review_surface as review_surface_module
import corpus_kb.tools.centroid_tools as centroid_tools_module
import corpus_kb.tools.cluster_tools as cluster_tools_module
import corpus_kb.tools.ingest_common as ingest_common_module
import corpus_kb.tools.ingest_url_tools as ingest_url_tools_module
import corpus_kb.tools.keyphrase_tools as keyphrase_tools_module
import corpus_kb.tools.labeling_gold_tools as labeling_gold_tools_module
import corpus_kb.tools.labeling_triage_tools as labeling_triage_tools_module
import corpus_kb.tools.lexical_tools as lexical_tools_module
import corpus_kb.tools.neighbor_tools as neighbor_tools_module
import corpus_kb.tools.nli_tools as nli_tools_module
import corpus_kb.tools.notebook_tools as notebook_tools_module
import corpus_kb.tools.research_tools as research_tools_module
import corpus_kb.tools.uxr_method_tools as uxr_method_tools_module
import corpus_kb.tools.vector_screen_tools as vector_screen_tools_module
from corpus_kb.analysis.method_register_dataflow import ladder_next
from corpus_kb.analysis.method_register_loader import load_method_register
from corpus_kb.api.http import create_http_app
from corpus_kb.api.socket import JSONRPCServer
from corpus_kb.contracts import OPERATIONS, SKIPPED_OPERATIONS
from corpus_kb.contracts.model import HTTP, Operation, surfaces_of
from corpus_kb.contracts.openapi import build_openapi
from corpus_kb.domain.models import RoutedResult, VerifyAnswerResult
from corpus_kb.handlers.query_handler import QueryHandler
from corpus_kb.handlers.query_integrity import DegradedExcluded, SearchEnvelope
from corpus_kb.utils.models import Entity, Relation
from corpus_kb.web.fetch import FetchedPage


class _LockConn:
    """Connection stub every pool hands out: writes OK, reads empty.

    The three canned probes keep the honest success shapes: the ingest
    advisory lock is free, ``to_regclass`` reports the ingest ledger
    present, and the information-schema probe reports BOTH degraded
    columns — so status/reembed return the real no-history success
    envelope, not the pre-migration "unavailable" one.
    """

    async def fetchval(self, sql: str, *args: Any) -> Any:
        if "pg_try_advisory_lock" in sql:
            return True
        if "to_regclass" in sql:
            return "public.ingest_runs"
        if "information_schema.columns" in sql:
            return 2
        return 0

    def __getattr__(self, name: str) -> Callable[..., Any]:
        # House fake pattern (attribute routing, as in test_extraction_tune):
        # execute is the tenant-GUC write (always OK); fetchrow routes the
        # single-row probes (the beliefs posterior, and every ingest_queue
        # statement, which answers the canned queue row below).
        if name == "execute":
            return self._ok
        if name == "fetchrow":
            return self._fetchrow
        raise AttributeError(name)

    async def _ok(self, *args: Any) -> str:
        return "OK"

    async def _fetchrow(self, sql: str, *args: Any) -> dict[str, Any]:
        if "ingest_queue" in sql:
            return dict(_QUEUE_ROW)
        if "research_projects" in sql:
            return {"project_id": PROJECT_ID}
        if "origin_project_id" in sql:
            return {"origin_project_id": None}
        if "project_claims" in sql:
            return {"project_id": PROJECT_ID}
        # The stored alpha/beta a rebuild pooled — beliefs_timeline and
        # beliefs_predict return the real success envelope against it.
        return {"alpha": 3.0, "beta": 1.0, "evidence_count": 4, "era_count": 1}

    async def fetch(self, sql: str, *args: Any) -> list[Any]:
        if "SET resolved_verdict" in sql:
            # The canned resolve: one flag row with fixed endpoint ids.
            return [
                {
                    "statement_a_id": "00000000-0000-0000-0000-00000000000a",
                    "statement_b_id": "00000000-0000-0000-0000-00000000000b",
                }
            ]
        if "sc.resolved_verdict = 'revision'" in sql:
            # The canned tune mine: one resolved statement with anchored
            # entities (the JSONB shape statement_row writes).
            return [
                {
                    "statement_id": "00000000-0000-0000-0000-00000000000a",
                    "statement_text": "I like ice cream.",
                    "entities": '[{"name": "ice cream", "entity_type": "CONCEPT"}]',
                }
            ]
        return []

    def transaction(self) -> _LockConn:
        return self

    async def __aenter__(self) -> _LockConn:
        return self

    async def __aexit__(self, *args: Any) -> bool:
        return False


class _FakePool:
    def __init__(self, conn: _LockConn | None = None) -> None:
        self._conn = conn if conn is not None else _LockConn()

    def acquire(self) -> _FakePool:
        return self

    async def __aenter__(self) -> Any:
        return self._conn

    async def __aexit__(self, *args: Any) -> bool:
        return False

    async def close(self) -> None:
        return None


class _StubStore:
    """Canned ingest store: nothing newly inserted, so embeddings never run."""

    def __init__(self, pool: Any, tenant_id: str = "t") -> None:
        self.unchanged: str | None = None

    async def unchanged_doc_id(self, *args: object) -> str | None:
        return self.unchanged

    async def unchanged_hash_doc_id(self, *args: object) -> str | None:
        return None

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
    """Stand-in eventsourcing app: records saves, reports no history.

    ``repository.get`` answers empty, so the delete audit finds no
    event-store history and attaches no keys — the same envelope the bare
    tool leg returns.
    """

    def __init__(self) -> None:
        self.saved: list[Any] = []
        self.repository = _EmptyRepository()

    def save(self, aggregate: Any) -> None:
        self.saved.append(aggregate)


class _EmptyRepository:
    def get(self, aggregate_id: Any) -> list[Any]:
        return []


class _StubRouter:
    async def handle_routed_query(self, query: Any) -> RoutedResult:
        return _ROUTED_RESULT


class _ParityGraphStore:
    """Canned GraphStore for the graph read ops: one entity, one relation.

    The graph reads route through ``get_graph_handler``; the fixture pins
    that singleton to this store so all four surfaces see one envelope.
    """

    async def search_entities(self, name: str, entity_type: str | None = None) -> list[Entity]:
        return [
            Entity(
                entity_id=ENTITY_A,
                name="parity-entity",
                entity_type=entity_type or "concept",
                source_type="code",
                metadata={},
            )
        ]

    async def get_entity(self, entity_id: str) -> Entity | None:
        return None

    async def get_entity_relations(self, entity_id: str) -> list[Relation]:
        return [
            Relation(
                relation_id="rel-1",
                source_entity_id=ENTITY_A,
                target_entity_id=ENTITY_B,
                relation_type="related_to",
                weight=1.0,
                metadata={},
            )
        ]

    async def neighbors(
        self,
        entity_id: str,
        hops: int = 1,
        direction: str = "both",
        relation_types: list[str] | None = None,
        entity_types: list[str] | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        return [
            {
                "entity_id": ENTITY_B,
                "name": "parity-neighbor",
                "entity_type": "concept",
                "metadata": {},
                "depth": 1,
            }
        ]

    async def traverse(
        self,
        entity_id: str,
        max_depth: int = 5,
        relation_types: list[str] | None = None,
        entity_types: list[str] | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        return [
            {
                "entity_id": ENTITY_B,
                "name": "parity-neighbor",
                "entity_type": "concept",
                "metadata": {},
                "depth": 1,
            }
        ]


class _ReleaseResult:
    """Stand-in for the frozen release result dataclasses (to_payload)."""

    def __init__(self, payload: dict[str, Any]) -> None:
        self._payload = payload

    def to_payload(self) -> dict[str, Any]:
        return dict(self._payload)


class _StubResearchHandler:
    async def ingest_transcript(
        self,
        tenant_id: UUID,
        file_path: str,
        project_id: UUID | None = None,
        title: str | None = None,
        force: bool = False,
        validate_mode: str = "off",
    ) -> dict[str, Any]:
        return dict(_TRANSCRIPT)

    async def ingest_path(
        self,
        tenant_id: UUID,
        path: str,
        project_id: UUID | None = None,
        force: bool = False,
        validate_mode: str = "off",
    ) -> dict[str, Any]:
        return dict(_INGEST_PATH)


class _StubLlmHandler:
    def __init__(self, config: Any = None) -> None:
        self.config = config


def _offline_config(profiles_dir: str | None = None) -> dict[str, Any]:
    config: dict[str, Any] = {
        "graph": {"extract_entities": False},
        "embedding": {"provider": "pgml"},
        "ingest": {"chunking": {"enabled": False}},
    }
    if profiles_dir is not None:
        config["profiles"] = {"dir": profiles_dir}
    return config


def _release_stub(payload: dict[str, Any]) -> Callable[..., Any]:
    async def stub(*args: Any, **kwargs: Any) -> _ReleaseResult:
        return _ReleaseResult(payload)

    return stub


@pytest.fixture
def parity_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> dict[str, Any]:
    """One stubbed world for all legs, torn down per test."""
    monkeypatch.setattr(ingest_common_module, "PostgresIngestStore", _StubStore)

    # Partition seam: the file legs never exercise unstructured here (the
    # pipeline's partition behavior is covered by the ingest tests). An
    # empty element list keeps the walk hermetic on hosts without libmagic.
    import corpus_kb.ingestion.strategies.default as default_strategy_module

    monkeypatch.setattr(
        default_strategy_module,
        "unstructured_partition",
        lambda *args: [],
    )

    # Calibrate apply (issue #200): one profile lock plus a small config
    # file, both under tmp_path, so no leg ever touches a real config.yaml.
    cal_profiles = tmp_path / "profiles"
    cal_profiles.mkdir()
    cal_profile = cal_profiles / "parity.yaml"
    cal_profile.write_text("data_type: transcript\n", encoding="utf-8")
    cal_lock = cal_profiles / "parity.lock.json"
    cal_lock.write_text(
        json.dumps(
            {
                "profile": "parity",
                "profile_sha256": hashlib.sha256(cal_profile.read_bytes()).hexdigest(),
                "outcome": "success",
                "chosen": "rrf10",
                "proposed_overrides": {"retrieval.rrf_k": 10},
            },
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    cal_config = tmp_path / "config.yaml"
    cal_config.write_text("retrieval:\n  rrf_k: 60\n", encoding="utf-8")
    monkeypatch.setattr(config_module, "config_file_path", lambda: cal_config)

    pool = _FakePool()
    saved = _SavedAggregate()
    monkeypatch.setattr(command_handler_module, "get_app_for_config", lambda _cfg: saved)
    monkeypatch.setattr(delete_audit_module, "get_app_for_config", lambda _cfg: saved)
    command_handler_module.reset_command_handler()
    command_handler_module.get_command_handler(config=_offline_config(), pool=pool)

    # Read side: one real (patched) handler per query method; every surface
    # singleton resolves to it, so all legs see identical envelopes.
    def fake_query_handler(pool: Any = None) -> QueryHandler:
        return QueryHandler(_FakePool(), config=_offline_config())

    async def canned_search(self: Any, query: Any) -> SearchEnvelope:
        # The engine index modes are guaranteed search-envelope keys
        # (contracts/envelopes.py SEARCH_ENVELOPE); the canned world is the
        # Postgres handler, so it states the Postgres modes explicitly.
        return SearchEnvelope(
            status="success",
            results=[],
            degraded_excluded=DegradedExcluded(),
            vector_index_mode="hnsw_ann",
            lexical_index_mode="gin_auto",
        )

    async def canned_verify(self: Any, query: Any) -> VerifyAnswerResult:
        return _VERIFY_RESULT

    async def canned_list_documents(self: Any, query: Any) -> list[Any]:
        return []

    # Issue #182 read ops: the similar/context searches answer through the
    # same canned QueryHandler; the tag/metadata reads answer through the
    # same canned TagHandler singleton.
    async def canned_search_similar(self: Any, query: Any) -> list[Any]:
        return []

    async def canned_search_context(self: Any, query: Any) -> list[Any]:
        return []

    async def canned_document_tags(self: Any, tenant_id: Any, doc_id: Any) -> list[Any]:
        return []

    async def canned_metadata(
        self: Any, tenant_id: Any, key: Any = None, doc_id: Any = None
    ) -> list[Any]:
        return []

    monkeypatch.setattr(query_handler_module, "get_query_handler", fake_query_handler)
    monkeypatch.setattr(QueryHandler, "handle_search_with_meta", canned_search)
    monkeypatch.setattr(QueryHandler, "handle_verify_answer", canned_verify)
    monkeypatch.setattr(QueryHandler, "handle_list_documents", canned_list_documents)
    monkeypatch.setattr(QueryHandler, "handle_search_similar", canned_search_similar)
    monkeypatch.setattr(QueryHandler, "handle_search_context", canned_search_context)
    monkeypatch.setattr(
        tag_handler_module, "get_tag_handler", lambda: tag_handler_module.TagHandler(_FakePool())
    )
    monkeypatch.setattr(
        tag_handler_module.TagHandler, "handle_get_document_tags", canned_document_tags
    )
    monkeypatch.setattr(tag_handler_module.TagHandler, "handle_get_metadata", canned_metadata)
    monkeypatch.setattr(router_handler_module, "get_router_handler", _StubRouter)

    # Graph read ops: one canned store behind the handler singleton, so the
    # MCP tool, the HTTP route, the socket arm, and the shared tool layer
    # all answer through the same GraphHandler.
    from corpus_kb.handlers.graph_handler import GraphHandler

    monkeypatch.setattr(
        graph_handler_module,
        "get_graph_handler",
        lambda: GraphHandler(_ParityGraphStore(), _offline_config()),
    )

    # Release lifecycle: the tool layer calls these handler names at call time.
    monkeypatch.setattr(release_handlers_module, "assess_release", _release_stub(_ASSESS))
    monkeypatch.setattr(release_handlers_module, "request_release", _release_stub(_REQUEST))
    monkeypatch.setattr(release_handlers_module, "review_release", _release_stub(_REVIEW))
    monkeypatch.setattr(release_handlers_module, "execute_release", _release_stub(_EXECUTE))
    monkeypatch.setattr(release_handlers_module, "status_report", _release_stub(_STATUS))
    monkeypatch.setattr(
        release_handlers_module, "compare_version_releases", _release_stub(_COMPARE)
    )

    # Issue #350 coding ops: the canned class methods answer every
    # surface (the tool layer builds CodingHandler over the caller's pool;
    # the routes resolve the singleton over the same fake pool).
    async def canned_embed(self: Any, tenant_id: Any, codebook: Any, embed_fn: Any = None):
        return dict(_EMBED_CODEBOOK)

    async def canned_pool(self: Any, tenant_id: Any, codebook_version_id: Any):
        return dict(_POOL)

    async def canned_code_batch(self: Any, tenant_id: Any, cells: Any, **kwargs: Any):
        return dict(_CODE_BATCH)

    async def canned_reliability(self: Any, tenant_id: Any, **kwargs: Any):
        return dict(_RELIABILITY)

    async def canned_calibrate(self: Any, tenant_id: Any, **kwargs: Any):
        return dict(_CALIBRATE)

    async def canned_saturation(self: Any, tenant_id: Any, **kwargs: Any):
        return dict(_SATURATION)

    monkeypatch.setattr(coding_handler_module.CodingHandler, "handle_embed_codebook", canned_embed)
    monkeypatch.setattr(coding_handler_module.CodingHandler, "handle_pool", canned_pool)
    monkeypatch.setattr(coding_handler_module.CodingHandler, "handle_code_batch", canned_code_batch)
    monkeypatch.setattr(
        coding_handler_module.CodingHandler, "handle_reliability", canned_reliability
    )
    monkeypatch.setattr(
        coding_handler_module.CodingHandler, "handle_calibrate_floors", canned_calibrate
    )
    monkeypatch.setattr(coding_handler_module.CodingHandler, "handle_saturation", canned_saturation)
    monkeypatch.setattr(
        coding_handler_module,
        "get_coding_handler",
        lambda: coding_handler_module.CodingHandler(_FakePool()),
    )

    # Research transcript + path ingest: stub the handler + the projection
    # catch-up.
    monkeypatch.setattr(
        research_tools_module,
        "get_research_handler",
        lambda pool, **kwargs: _StubResearchHandler(),
    )

    async def noop_advance(pool: Any, tenant_id: UUID) -> None:
        return None

    monkeypatch.setattr(research_tools_module, "advance_all_projections", noop_advance)

    # Promoted research ops (full surface parity wave): stub at the exact
    # seam the shared tool layer imports at call time.
    monkeypatch.setattr(config_module, "load_config", lambda: _offline_config(str(cal_profiles)))
    monkeypatch.setattr(llm_handler_module, "LlmHandler", _StubLlmHandler)

    async def stub_result(payload: dict[str, Any], *args: Any, **kwargs: Any) -> dict[str, Any]:
        return payload

    monkeypatch.setattr(deductive_run_module, "run_deductive", lambda *a, **k: stub_result(_CODING))
    monkeypatch.setattr(
        inductive_run_module, "run_inductive", lambda *a, **k: stub_result(_INDUCTIVE)
    )
    monkeypatch.setattr(
        promote_code_module, "promote_proposal", lambda *a, **k: stub_result(_PROMOTE)
    )

    async def stub_report(*args: Any, **kwargs: Any) -> object:
        return object()

    monkeypatch.setattr(report_runner_module, "build_report", stub_report)
    monkeypatch.setattr(governance_report_module, "to_dict", lambda report: dict(_REPORT))
    monkeypatch.setattr(
        review_surface_module, "execute_review", lambda *a, **k: stub_result(_REVIEWED)
    )
    monkeypatch.setattr(
        notebook_tools_module, "evidence_for_code", lambda *a, **k: stub_result(_EVIDENCE)
    )
    monkeypatch.setattr(
        notebook_tools_module, "uncoded_units", lambda *a, **k: stub_result(_UNCODED)
    )
    monkeypatch.setattr(
        notebook_tools_module, "overlap_view", lambda *a, **k: stub_result(_OVERLAP)
    )

    async def stub_exit_zero(*args: Any, **kwargs: Any) -> int:
        return 0

    monkeypatch.setattr(research_cycle_module, "run_cycle", stub_exit_zero)

    async def stub_checkpoint(*args: Any, **kwargs: Any) -> dict[str, Any]:
        return {"kind": "final"}

    monkeypatch.setattr(research_cycle_state_module, "last_cycle_checkpoint", stub_checkpoint)
    monkeypatch.setattr(research_demo_module, "run_demo", stub_exit_zero)

    # Notebook ask: retrieval-only leg; stub the embedder boundary + handler.
    async def canned_ask(pool: Any, embedder: Any, llm: Any, query: Any) -> dict[str, Any]:
        return dict(_ASK)

    monkeypatch.setattr(notebook_tools_module, "notebook_ask", canned_ask)
    monkeypatch.setattr(rag_module, "create_embedder", lambda cfg, pool: None)
    monkeypatch.setattr(research_embed_module, "ResearchEmbedder", lambda pool, embedder: object())

    # Ingest URL: canned fetch + canned pipeline (the aggregation envelope is
    # the surface contract; the pipeline itself is covered by ingest_text).
    async def fake_fetch_page(url: str, config: Any, transport: Any = None) -> FetchedPage:
        return FetchedPage(
            url=url,
            final_url=url,
            status_code=200,
            html=_URL_HTML,
            content_type="text/html",
            fetched_at="2026-10-13T00:00:00+00:00",
        )

    async def fake_run_pipeline(
        text: str,
        source_type: str,
        path: str,
        config: Any,
        pool: Any,
        tenant_id: str,
        rag_backend: Any = None,
        is_file: bool = False,
        strategy_name: str | None = None,
    ) -> dict[str, Any]:
        return {"status": "success", "document_id": "doc-1"}

    monkeypatch.setattr(ingest_url_tools_module, "fetch_page", fake_fetch_page)
    monkeypatch.setattr(ingest_url_tools_module, "run_pipeline", fake_run_pipeline)

    # -- AMR method ops: one canned vector space + the family's own test
    #    seams. The vector math itself (numpy) runs for real; the optional
    #    extras (sklearn/umap) never load in this world — CI has none. ----
    from corpus_kb.analysis.vectors import VectorSpace
    from corpus_kb.labeling.engine import StoredChunk

    def _canned_space() -> VectorSpace:
        matrix = np.array(
            [
                [1.0, 0.0],
                [0.9, 0.1],
                [0.0, 1.0],
                [1.0, 1.0],
                [0.2, 0.8],
                [0.8, 0.2],
            ],
            dtype=np.float64,
        )
        return VectorSpace(
            ids=[f"chunk-{index}" for index in range(matrix.shape[0])],
            matrix=matrix,
            excluded_zero_count=0,
        )

    async def _canned_vectors(
        pool: Any, tenant_id: str, config: dict[str, Any] | None
    ) -> VectorSpace:
        return _canned_space()

    async def _canned_space_texts(
        pool: Any, tenant_id: str, config: dict[str, Any] | None
    ) -> tuple[VectorSpace, dict[str, str]]:
        space = _canned_space()
        return space, {chunk_id: f"parity text {chunk_id}" for chunk_id in space.ids}

    async def _canned_labels(
        pool: Any, tenant_id: str, limit: int | None = None
    ) -> list[dict[str, Any]]:
        rows = []
        for index in range(4):
            rows.append(
                {
                    "chunk_id": f"chunk-{index}",
                    "label": "reports" if index < 2 else "meetings",
                    "probability": 0.9,
                }
            )
        return rows

    for vector_module in (
        vector_screen_tools_module,
        neighbor_tools_module,
        centroid_tools_module,
        cluster_tools_module,
    ):
        monkeypatch.setattr(vector_module, "_load_vectors", _canned_vectors)
    monkeypatch.setattr(neighbor_tools_module, "_load_labels", _canned_labels)
    monkeypatch.setattr(centroid_tools_module, "_load_labels", _canned_labels)

    def _stub_reduce(matrix: np.ndarray) -> tuple[np.ndarray, str]:
        return matrix[:, :2], "stub"

    def _stub_cluster(reduced: np.ndarray, min_cluster_size: int) -> np.ndarray:
        return np.array([0, 0, 1, 1, -1, 0])

    monkeypatch.setattr(cluster_tools_module, "_reduce", _stub_reduce)
    monkeypatch.setattr(cluster_tools_module, "_cluster", _stub_cluster)

    # nli: canned space + canned verdicts keep the transport offline.
    monkeypatch.setattr(nli_tools_module, "_load_space", _canned_space_texts)
    monkeypatch.setattr(uxr_method_tools_module, "_load_space", _canned_space_texts)

    async def _canned_verdict(prompt: str, model: str, base_url: str) -> str:
        return "ENTAIL"

    async def _canned_batch_verdicts(
        prompt: str, statements: dict[str, str], model: str, base_url: str
    ) -> dict[str, str]:
        return dict.fromkeys(statements, "ENTAIL")

    monkeypatch.setattr(nli_contract_module, "judge_verdict", _canned_verdict)
    monkeypatch.setattr(nli_contract_module, "judge_batch_verdicts", _canned_batch_verdicts)

    # keyphrases: canned candidates keep the tfidf arm sklearn-free.
    def _canned_candidates(_texts: list[str]) -> dict[str, float]:
        return {"parity phrase": 0.7, "alpha term": 0.4}

    monkeypatch.setattr(keyphrase_tools_module, "_candidate_phrases", _canned_candidates)

    # lexical: skip the [classify] extra and pin the three read seams.
    async def _tables_present(pool: Any) -> bool:
        return True

    async def _canned_chunks(
        pool: Any, tenant_id: str, limit: int | None = None
    ) -> list[StoredChunk]:
        return [
            StoredChunk(chunk_id=f"chunk-{index}", text=f"parity text {index}", metadata={})
            for index in range(6)
        ]

    def _stub_fit_and_score(
        labeled: list[tuple[str, str, str]],
        classes: list[str],
        corpus: list[tuple[str, str]],
    ) -> list[dict[str, Any]]:
        return [
            {"chunk_id": chunk_id, "label": classes[index % len(classes)], "probability": 0.9}
            for index, (chunk_id, _text) in enumerate(corpus)
        ]

    monkeypatch.setattr(lexical_tools_module, "ensure_sklearn", lambda: None)
    monkeypatch.setattr(lexical_tools_module, "_fit_and_score", _stub_fit_and_score)
    monkeypatch.setattr(labeling_writer_module, "labeling_tables_present", _tables_present)
    monkeypatch.setattr(labeling_writer_module, "read_labels", _canned_labels)
    monkeypatch.setattr(labeling_engine_module, "read_chunks", _canned_chunks)

    # Label-quality pair: the same canned labeling world feeds the triage
    # (vectors + labels seams) and the gold check (labels seam + file).
    monkeypatch.setattr(labeling_triage_tools_module, "_load_vectors", _canned_vectors)
    monkeypatch.setattr(labeling_triage_tools_module, "_load_labels", _canned_labels)
    monkeypatch.setattr(labeling_gold_tools_module, "_load_labels", _canned_labels)
    gold_path = tmp_path / "gold.json"
    gold_path.write_text(
        json.dumps(
            {
                "chunk-0": "reports",
                "chunk-1": "meetings",
                "chunk-2": "meetings",
                "chunk-99": "reports",  # off-build id: counted as ignored
            }
        ),
        encoding="utf-8",
    )

    # sentiment + active-learning + the selector answer their honest
    # default paths on this world (disabled flag, empty store, real
    # register) — no stubs beyond the shared offline config.

    # The pool the research/release/ingest-front-door HTTP routes resolve.
    research_http_module.set_research_pool(pool)

    file_path = tmp_path / "parity.txt"
    file_path.write_text(TEXT_BODY, encoding="utf-8")

    # Wave 4A: one registry-generated MCP server over the SAME stubbed pool
    # and config — the mcp leg proves the generated tools return the shared
    # envelope.
    from corpus_kb.mcp_server import build_mcp_server

    yield {
        "pool": pool,
        "config": _offline_config(str(cal_profiles)),
        "client": TestClient(create_http_app()),
        "server": JSONRPCServer(),
        "mcp_server": build_mcp_server(pool, _offline_config(str(cal_profiles))),
        _FILE_PATH: file_path,
        _DIR_PATH: tmp_path,
        _GOLD_PATH: gold_path,
    }
    command_handler_module.reset_command_handler()
    research_http_module.set_research_pool(None)


# ---------------------------------------------------------------------------
# The gates
# ---------------------------------------------------------------------------


def test_catalogue_and_legs_agree() -> None:
    """The catalogue, the legs, and the SKIP set lock each other."""
    op_names = {op.name for op in OPERATIONS}
    skip_names = {skip.name for skip in SKIPPED_OPERATIONS}
    assert not op_names & skip_names, "an op cannot be both tested and skipped"
    assert set(LEGS) == op_names, f"catalogue/legs drift: {sorted(op_names ^ set(LEGS))}"
    declared_kinds = {"cli", "http", "mcp", "socket"}
    for op in OPERATIONS:
        # Declared surface parity: every operation carries a leg for each
        # surface KIND its row declares. A row may declare a reduced set
        # (the multipart upload is HTTP-only by nature); the exemption
        # lives in the row, so a missing non-declared leg is declared-
        # exempt, never a silent skip.
        kinds = {surface.kind for surface in op.surfaces}
        assert kinds <= declared_kinds, (
            f"{op.name} declares unknown surface kinds {sorted(kinds - declared_kinds)}"
        )
        assert "http" in kinds, f"{op.name} must declare the HTTP surface"
        if "socket" in kinds:
            assert LEGS[op.name].socket is not None, f"{op.name} needs a socket leg"
        # Every catalogue MCP surface gets an MCP leg (and only those do) —
        # the MCP server is generated from the same rows.
        has_surface = bool(surfaces_of(op, "mcp"))
        has_leg = LEGS[op.name].mcp is not None
        assert has_surface == has_leg, (
            f"{op.name}: MCP surface/leg mismatch (surface={has_surface}, leg={has_leg})"
        )


async def test_demo_load_conditional_keys_stay_declared(
    parity_env: dict[str, Any],
) -> None:
    """The demo receipt carries only its declared conditional keys (F8).

    The #415 ``degraded`` key shipped with no contract row, so nothing
    failed when the receipt grew. The catalogue row pins the fixed
    top-level shape; this pin holds the receipt's conditional keys to
    DEMO_KEYS — an undeclared key on a future run fails here.
    """
    from corpus_kb.contracts.envelopes import DEMO_ENVELOPE, DEMO_KEYS

    envelope = await _shared_demo_load(parity_env)
    result = envelope["result"]
    assert isinstance(result, dict)
    assert set(envelope) == set(DEMO_ENVELOPE)
    # Always present on every run (demo_kb._envelope).
    assert {"ingested", "skipped", "chunk_counts"} <= set(result)
    # Conditional keys only ever come from the declared set.
    assert set(result) <= {"ingested", "skipped", "chunk_counts"} | DEMO_KEYS


def test_catalogue_matches_surface_registry() -> None:
    """Rows whose op exists in surface_registry must byte-match its columns.

    The comparison reads each registry row's DECLARED surface set: a
    declared transport byte-matches the catalogue surface; an undeclared
    transport must have NO catalogue surface (the multipart upload's
    HTTP-only row is the live example).
    """
    from corpus_kb.surface_registry import SURFACES

    registry = {surface.name.replace("-", "_"): surface for surface in SURFACES}
    checked: list[str] = []
    for op in OPERATIONS:
        surface = registry.get(op.name)
        if surface is None:
            continue
        declared = set(getattr(surface, "surfaces", None) or ("cli", "mcp", "http"))
        mcp = surfaces_of(op, "mcp")
        cli = surfaces_of(op, "cli")
        http = surfaces_of(op, HTTP)
        if "mcp" in declared:
            assert mcp and mcp[0].route == surface.mcp_tool, op.name
        if "cli" in declared:
            assert cli and cli[0].route == surface.cli_path, op.name
        if "http" in declared:
            assert http and http[0].route == surface.http_route, op.name
        if "mcp" not in declared:
            assert not mcp, op.name
        if "cli" not in declared:
            assert not cli, op.name
        if "http" not in declared:
            assert not http, op.name
        checked.append(op.name)
    # The check must stay meaningful: these pinned rows prove it ran.
    assert {
        "release_assess",
        "release_status",
        "ingest_status",
        "ingest_directory",
        "ingest_url",
        # U3 (#202): the bulk re-embed row byte-matches its registry row.
        "ingest_reingest",
        "topics_build",
        "topics_list",
        # Analysis program Wave 0 (the registry skeleton rows).
        "topics_trends",
        "labeling_build",
        "labeling_list",
        "labeling_lfs",
        # Label-quality pair (issue #234: the WP-2 modules wired).
        "labeling_triage",
        "labeling_gold_check",
        "classification_build",
        "classification_list",
        "analysis_backfill_dates",
        "analyze_run",
        # Analysis method register program (wave 2 method rows).
        "analysis_select_method",
        # Analysis question-routing program (wave 3 question front door).
        "analysis_route",
        "keyphrases_extract",
        "lexical_classify",
        "neighbors_classify",
        "centroids_classify",
        "clusters_inspect",
        "duplicates_screen",
        "outliers_rank",
        "nli_classify",
        "contradictions_screen",
        "sentiment_screen",
        "active_learn_next",
        "research_ingest_transcript",
        "notebook_ask",
        "research_ingest",
        "coding_run",
        "codebook_promote",
        "notebook_evidence",
        "research_cycle",
        "research_demo",
        # Issue #182: the search read pair byte-matches its registry rows
        # (documents-list/verify/document-delete/document-tags/document-
        # metadata use the UI manifest ids, so no op name coincides).
        "search_similar",
        "search_context",
        # Issue #200: the calibrate apply row byte-matches its registry row.
        "calibrate_apply",
        # Track C: the per-project raw-table op rows byte-match.
        "project_provision",
        "project_list",
        "project_drop",
        "project_claim",
        "project_claim_list",  # W4: the claim rows byte-match their registry rows.
        # Issue #350: the coding + events rows byte-match their registry rows.
        "coding_embed_codebook",
        "coding_pool",
        "coding_code_batch",
        "coding_reliability",
        "coding_calibrate_floors",
        "coding_saturation",
        "coding_status",
        "coding_export",
        "events_summary",
        # Ruling 4a: the HTTP-only upload row byte-matches on its declared
        # surface (the empty cli/mcp registry fields are exempt).
        "ingest_upload",
        # Momus F8 close-out: the demo-load row byte-matches its registry
        # row (cli demo-kb, MCP demo_load, POST /api/demo/load).
        "demo_load",
    } <= set(checked)


def _assert_parity(op: str, keys: dict[str, set[str]], allowed: dict[str, set[str]]) -> None:
    """Fail loudly, printing the exact drifting keys per surface."""
    base = keys["shared"]
    problems = []
    for surface in sorted(k for k in keys if k != "shared"):
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


@pytest.mark.parametrize("op", OPERATIONS, ids=lambda op: op.name)
async def test_envelope_parity(op: Operation, parity_env: dict[str, Any]) -> None:
    ctx = parity_env
    leg = LEGS[op.name]

    shared_env = await leg.shared(ctx)
    assert isinstance(shared_env, dict)
    declared_missing = sorted(set(op.envelope_keys) - set(shared_env))
    assert not declared_missing, (
        f"{op.name}: catalogue envelope keys absent on the shared leg: {declared_missing}"
    )

    keys: dict[str, set[str]] = {"shared": set(shared_env)}
    allowed: dict[str, set[str]] = {}

    spec = leg.http(ctx)
    client: TestClient = ctx["client"]
    if spec["method"] == "post":
        response = client.post(spec["path"], json=spec.get("json"))
    elif spec["method"] == "post_multipart":
        response = client.post(spec["path"], files=spec["files"])
    elif spec["method"] == "delete":
        response = client.delete(spec["path"])
    else:
        response = client.get(spec["path"], params=spec.get("params"))
    assert response.status_code == 200, response.text
    keys["http"] = set(response.json())
    allowed["http"] = set(op.http_extra)

    if leg.socket is not None:
        method, params = leg.socket(ctx)
        server: JSONRPCServer = ctx["server"]
        rpc = await server._process_request(
            json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params})
        )
        assert "result" in rpc, rpc
        keys["socket"] = set(rpc["result"])
        allowed["socket"] = set(op.socket_extra)

    if leg.mcp is not None:
        # The MCP leg: in-memory client against the generated server. MCP
        # adds NO envelope keys — the tool functions ARE the envelope — so
        # the allowed-extra set stays empty and drift fails loudly.
        payload = await _call_mcp_tool(ctx["mcp_server"], op.name, leg.mcp(ctx))
        keys["mcp"] = set(payload)
        allowed["mcp"] = set()

    _assert_parity(op.name, keys, allowed)


# ---------------------------------------------------------------------------
# Issue #349: the SQL query route refuses writes, on every surface
# ---------------------------------------------------------------------------


_SQL_REFUSAL_CASES = [
    pytest.param("SELECT 1", True, id="select-ok"),
    pytest.param("DELETE FROM documents", False, id="delete-plain"),
    pytest.param(
        "WITH x AS (DELETE FROM chunks RETURNING 1) SELECT * FROM x", False, id="cte-delete"
    ),
    pytest.param("SELECT 1; DROP TABLE tags", False, id="two-statements"),
]


@pytest.mark.parametrize(("sql", "allowed"), _SQL_REFUSAL_CASES)
async def test_sql_route_write_refusal_on_http_and_socket(
    sql: str, allowed: bool, parity_env: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """The three Check refusals + the happy path, on the HTTP leg, and the
    typed error's ONE socket code on the socket leg.

    The route is HTTP-only (no catalogue row, no socket method), so the
    socket leg pins the error-class contract instead of a dispatch: the
    ``WriteRefusedError`` that the shared handler raises must reach
    ``JSONRPCServer._process_request`` and answer exactly its taxonomy
    socket code there, the same failure class the HTTP leg answers 400
    with ``error_type: write_refused``.
    """
    client: TestClient = parity_env["client"]
    response = client.post("/api/query/sql", json={"sql": sql})
    if allowed:
        assert response.status_code == 200, response.text
        assert response.json() == {"status": "success", "result": []}
    else:
        assert response.status_code == 400, response.text
        body = response.json()
        assert body["status"] == "error"
        assert body["error_type"] == "write_refused"
        assert body["error"]

    # The socket surface answers the same failure class with its ONE code.
    import corpus_kb.handlers.query_handler as query_handler_module
    from corpus_kb.errors import ERROR_MAP, WriteRefusedError

    class _Refuser:
        async def handle_verify_answer(self, _query: object) -> object:
            raise WriteRefusedError("The SQL route accepts read statements only.")

    monkeypatch.setattr(query_handler_module, "get_query_handler", lambda *a, **k: _Refuser())
    server: JSONRPCServer = parity_env["server"]
    rpc = await server._process_request(
        json.dumps({"jsonrpc": "2.0", "id": 1, "method": "verify_answer", "params": {}})
    )
    assert "error" in rpc, rpc
    assert rpc["error"]["code"] == ERROR_MAP["write_refused"][1]
    assert rpc["error"]["message"]


async def test_analysis_route_suggestions_parity(parity_env: dict[str, Any]) -> None:
    """The next-question suggestions ride the shared handler on all legs.

    Every leg answers deterministically: the fixture patches
    ``corpus_kb.config.load_config`` to the offline pgml config, so the
    HTTP and socket adapters take the phrase path too, and the leg
    question resolves to method 29. That row authors three ``suggests``
    strings, so all four legs must carry exactly those strings verbatim.
    A surface that drops, rewords, or re-sources them fails.
    """
    ctx = parity_env
    leg = LEGS["analysis_route"]
    shared = await leg.shared(ctx)

    client: TestClient = ctx["client"]
    response = client.post("/api/analysis/route", json={"question": _ROUTE_QUESTION})
    assert response.status_code == 200, response.text
    http_env: dict[str, Any] = response.json()

    server: JSONRPCServer = ctx["server"]
    rpc = await server._process_request(
        json.dumps(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "analysis_route",
                "params": {"question": _ROUTE_QUESTION},
            }
        )
    )
    assert "result" in rpc, rpc
    socket_env: dict[str, Any] = rpc["result"]

    mcp_env = await _call_mcp_tool(
        ctx["mcp_server"], "analysis_route", {"question": _ROUTE_QUESTION}
    )

    register = load_method_register({})
    chosen = next(row for row in register.methods if row.id == "29")
    expected = list(chosen.suggests or [])[:3]
    assert len(expected) == 3, "the leg method must author three suggestions"

    for name, envelope in (
        ("shared", shared),
        ("http", http_env),
        ("socket", socket_env),
        ("mcp", mcp_env),
    ):
        assert envelope["suggestions"] == expected, name
        intent = envelope["intent"]
        assert intent["method_id"] == "29", name


async def test_analysis_route_next_methods_parity(parity_env: dict[str, Any]) -> None:
    """The next-step chain key rides the shared handler on all legs.

    The catalogue tuple declares ``next_methods`` unconditional, so every
    leg must carry the key with the SAME value. The fixture's route is
    the degraded phrase path (the pgml config pins it), and the leg
    method authors no ``next`` chain, so the honest value on all four
    legs is ``[]`` — presence and equality are the contract here; the
    chain content is pinned in ``tests/test_analysis_route_next.py``.
    """
    ctx = parity_env
    leg = LEGS["analysis_route"]
    shared = await leg.shared(ctx)

    client: TestClient = ctx["client"]
    response = client.post("/api/analysis/route", json={"question": _ROUTE_QUESTION})
    assert response.status_code == 200, response.text
    http_env: dict[str, Any] = response.json()

    server: JSONRPCServer = ctx["server"]
    rpc = await server._process_request(
        json.dumps(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "analysis_route",
                "params": {"question": _ROUTE_QUESTION},
            }
        )
    )
    assert "result" in rpc, rpc
    socket_env: dict[str, Any] = rpc["result"]

    mcp_env = await _call_mcp_tool(
        ctx["mcp_server"], "analysis_route", {"question": _ROUTE_QUESTION}
    )

    register = load_method_register({})
    expected = ladder_next(register.methods, "29")[:3]
    assert expected == [], "the leg method must author no chain (honest empty list)"

    for name, envelope in (
        ("shared", shared),
        ("http", http_env),
        ("socket", socket_env),
        ("mcp", mcp_env),
    ):
        assert envelope["next_methods"] == expected, name


async def _call_mcp_tool(
    mcp_server: Any, tool_name: str, arguments: dict[str, Any]
) -> dict[str, Any]:
    """One tools/call round-trip over the SDK's in-memory transport."""
    from mcp.client._memory import InMemoryTransport
    from mcp.client.session import ClientSession

    transport = InMemoryTransport(mcp_server)
    async with (
        transport as (read_stream, write_stream),
        ClientSession(read_stream, write_stream) as session,
    ):
        await session.initialize()
        result = await session.call_tool(tool_name, arguments)
    assert not result.is_error, f"{tool_name}: {result.content[0].text}"
    return cast("dict[str, Any]", json.loads(result.content[0].text))


def test_inventory_covers_catalogue() -> None:
    """`corpus-kb operations` is generated from the same catalogue rows."""
    from corpus_kb.cli.cli_operations import build_inventory

    inventory = build_inventory()
    rows = {row["name"]: row for row in inventory["operations"]}
    assert set(rows) == {op.name for op in OPERATIONS}
    skips = {skip["name"] for skip in inventory["skipped"]}
    assert skips == {skip.name for skip in SKIPPED_OPERATIONS}
    for op in OPERATIONS:
        row = rows[op.name]
        assert row["envelope_keys"] == list(op.envelope_keys)
        assert {p["name"] for p in row["params"]} == {p.name for p in op.params}
        assert row["parity"] == "tested"


def test_openapi_covers_http_surface() -> None:
    """The generated OpenAPI document lists every catalogue HTTP route."""
    doc = build_openapi(OPERATIONS)
    assert doc["openapi"].startswith("3.1")
    http_routes = {surface.route for op in OPERATIONS for surface in surfaces_of(op, HTTP)}
    assert http_routes <= set(doc["paths"])
    for path, item in doc["paths"].items():
        for method, operation_object in item.items():
            assert operation_object["operationId"], path
            assert "200" in operation_object["responses"], (path, method)
    schemas = doc["components"]["schemas"]
    assert "SuccessEnvelope" in schemas and "ErrorEnvelope" in schemas
    pytest.importorskip("openapi_spec_validator")  # dev-only manual check
    from openapi_spec_validator import validate

    validate(doc)


def test_operations_cli_emits_json() -> None:
    """The registered `operations` command serves both payloads over Typer."""
    from typer.testing import CliRunner

    from corpus_kb.cli import app

    runner = CliRunner()
    compact = runner.invoke(app, ["operations", "--indent", "0"])
    assert compact.exit_code == 0, compact.output
    doc = json.loads(compact.stdout)
    assert doc["generated_from"] == "corpus_kb.contracts"
    assert {row["name"] for row in doc["operations"]} == {op.name for op in OPERATIONS}

    spec = runner.invoke(app, ["operations", "--openapi"])
    assert spec.exit_code == 0, spec.output
    assert json.loads(spec.stdout)["openapi"].startswith("3.1")
