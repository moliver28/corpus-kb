"""Zero-drift flagship gate: envelope parity across shared tool / HTTP / socket / MCP.

The catalogue in ``corpus_kb.contracts`` is THE source of truth: this test
and ``corpus-kb operations`` iterate the SAME rows, so they cannot diverge.
For each catalogue operation the SAME arguments go through the legs the
catalogue declares:

  (a) ``shared``  — the ONE shared function every surface must route
      through (the tool layer; for handler-paired socket/HTTP ops it pins
      the shared envelope construction);
  (b) ``http``    — the Starlette route via TestClient (stubbed pool);
  (c) ``socket``  — the JSON-RPC request-processing entry point;
  (d) ``mcp``     — the registry-generated MCP server (Wave 4A) called
      through the SDK's in-memory transport, no stdio subprocess.

Per operation the gate asserts:

  1. SUCCESS envelope key-sets are equal across legs, minus the additive
     keys the catalogue declares for that surface (``http_extra`` /
     ``socket_extra``). An undeclared extra key on ONE surface fails with
     a printed diff. The MCP surface declares NO extras: the tool
     functions ARE the envelope, so its key-set must equal the shared
     leg's exactly.
  2. The catalogue's ``envelope_keys`` are all present on the shared leg,
     so the documented contract holds on the real envelopes.

Sync rules (program standard 2, registry-first): a catalogue row without
legs (or legs without a row) fails ``test_catalogue_and_legs_agree``, and
rows whose operation exists in ``surface_registry`` must byte-match its
CLI path, MCP tool name, and HTTP route. Ops that cannot run hermetically
live in the catalogue's ``SKIPPED_OPERATIONS`` with an honest reason.

Fully offline: the DB layer is stubbed at the same seams
tests/test_ingest_integrity.py and tests/test_search_honesty.py use; the
release/research handlers are stubbed at the handler seam.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast
from uuid import UUID

import numpy as np
import pytest
from starlette.testclient import TestClient

import corpus_kb.analysis.nli_contract as nli_contract_module
import corpus_kb.api.research_http as research_http_module
import corpus_kb.config as config_module
import corpus_kb.handlers.command_handler as command_handler_module
import corpus_kb.handlers.delete_audit as delete_audit_module
import corpus_kb.handlers.graph_handler as graph_handler_module
import corpus_kb.handlers.llm_handler as llm_handler_module
import corpus_kb.handlers.query_handler as query_handler_module
import corpus_kb.handlers.release_handlers as release_handlers_module
import corpus_kb.handlers.router_handler as router_handler_module
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
import corpus_kb.tools.active_learn_tools as active_learn_tools_module
import corpus_kb.tools.analysis_tools as analysis_tools_module
import corpus_kb.tools.beliefs_tools as beliefs_tools_module
import corpus_kb.tools.centroid_tools as centroid_tools_module
import corpus_kb.tools.classification_tools as classification_tools_module
import corpus_kb.tools.cluster_tools as cluster_tools_module
import corpus_kb.tools.conflicts_tools as conflicts_tools_module
import corpus_kb.tools.extraction_tune_tools as extraction_tune_tools_module
import corpus_kb.tools.ingest_common as ingest_common_module
import corpus_kb.tools.ingest_surface_tools as ingest_surface_tools_module
import corpus_kb.tools.ingest_url_tools as ingest_url_tools_module
import corpus_kb.tools.keyphrase_tools as keyphrase_tools_module
import corpus_kb.tools.labeling_tools as labeling_tools_module
import corpus_kb.tools.lexical_tools as lexical_tools_module
import corpus_kb.tools.meta_embed_tools as meta_embed_tools_module
import corpus_kb.tools.method_select_tools as method_select_tools_module
import corpus_kb.tools.neighbor_tools as neighbor_tools_module
import corpus_kb.tools.nli_tools as nli_tools_module
import corpus_kb.tools.notebook_tools as notebook_tools_module
import corpus_kb.tools.release_tools as release_tools_module
import corpus_kb.tools.research_tools as research_tools_module
import corpus_kb.tools.search_tools as search_tools_module
import corpus_kb.tools.sentiment_tools as sentiment_tools_module
import corpus_kb.tools.statements_tools as statements_tools_module
import corpus_kb.tools.topics_tools as topics_tools_module
import corpus_kb.tools.vector_screen_tools as vector_screen_tools_module
from corpus_kb.api.http import create_http_app
from corpus_kb.api.socket import JSONRPCServer
from corpus_kb.contracts import OPERATIONS, SKIPPED_OPERATIONS
from corpus_kb.contracts.model import HTTP, Operation, surfaces_of
from corpus_kb.contracts.openapi import build_openapi
from corpus_kb.domain.models import RouteDecision, RoutedQuery, RoutedResult, VerifyAnswerResult
from corpus_kb.handlers.query_handler import QueryHandler
from corpus_kb.handlers.query_integrity import DegradedExcluded, SearchEnvelope
from corpus_kb.tools.ingest_tools import list_documents as list_documents_tool
from corpus_kb.utils.models import Entity, Relation
from corpus_kb.web.fetch import FetchedPage

TENANT = "00000000-0000-0000-0000-000000000001"
CV = "11111111-1111-1111-1111-111111111111"  # codebook version id
RELEASE_ID = "33333333-3333-3333-3333-333333333333"
PROJECT_ID = "44444444-4444-4444-4444-444444444444"
ASSIGNMENT_ID = "77777777-7777-7777-7777-777777777777"
ENTITY_A = "55555555-5555-5555-5555-555555555555"
ENTITY_B = "66666666-6666-6666-6666-666666666666"

TEXT_BODY = "Corpus parity seed document. " * 4
_URL_HTML = "<html><body><p>parity page body</p></body></html>"

_VERIFY_RESULT = VerifyAnswerResult(verdicts=[], groundedness=1.0, abstained=True)
_ROUTED_RESULT = RoutedResult(
    decision=RouteDecision(route="semantic", confidence=0.9, fallback=False), results=[]
)

# Canned release payloads: the key sets the handlers' to_payload() returns.
_ASSESS = {
    "status": "assessed",
    "codebook_version_id": CV,
    "profile": None,
    "enforcement": "enforce",
    "gate_results": [],
    "required_gate_ids": [],
    "required_failing": [],
    "required_not_evaluable": [],
    "all_required_pass_without_waivers": True,
    "data_sufficiency": {},
}
_REQUEST = {
    "status": "draft_candidate",
    "release_id": RELEASE_ID,
    "manifest_sha256": "abc",
    "profile": None,
}
_REVIEW = {
    "status": "review_packet",
    "release_id": RELEASE_ID,
    "release_state": "draft_candidate",
    "profile": None,
    "enforcement": "enforce",
    "evidence": [],
    "failing_gates": [],
    "not_evaluable_gates": [],
    "waived_gates": [],
    "unmet_required_gates": [],
    "next_actions": [],
}
_EXECUTE = {
    "status": "released",
    "release_id": RELEASE_ID,
    "approver": "parity",
    "enforcement": "enforce",
    "unmet_required_gates": [],
    "warnings": [],
    "reason": None,
}
_STATUS = {
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
_COMPARE = {"status": "compared", "old_release_id": RELEASE_ID, "new_release_id": RELEASE_ID}

_TRANSCRIPT = {
    "status": "success",
    "doc_id": "doc-1",
    "path": "interview.txt",
    "n_turns": 2,
    "n_exchanges": 1,
    "source_type": "transcript",
    "parse_quality": "good",
    "file_sha256": "abc",
}
_ASK = {"status": "answer", "sentences": ["parity sentence."], "evidence": [], "llm_calls": 1}

# Canned envelopes for the promoted research ops (full surface parity wave):
# the key sets the shared tool layer really returns.
_INGEST_PATH = {
    "status": "success",
    "files_found": 1,
    "ingested": 1,
    "skipped": 0,
    "errors": 0,
    "results": [],
    "profile": None,
}
_CODING = {
    "status": "success",
    "run_id": "run-1",
    "codebook_version_id": CV,
    "n_units": 3,
    "n_codes": 2,
    "explicit": 1,
    "question_dependent": 1,
    "review": 1,
}
_INDUCTIVE = {"status": "success", "run_id": "run-1", "n_units": 3}
_PROMOTE = {
    "status": "promoted",
    "proposal_id": 1,
    "codebook_version_id": CV,
    "code_id": "code-1",
    "n_members": 3,
    "tau_dup": 0.8,
    "max_cos": 0.5,
    "rederivation": "k-medoids",
}
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
# The schema-pinned artifact: the real SCHEMA_VERSION or validate_required
# reports the report itself missing.
_REPORT = {"schema_version": "1.0", **{key: {} for key in _REQUIRED_REPORT_KEYS}}
_REVIEWED = {
    "status": "success",
    "assignment_id": ASSIGNMENT_ID,
    "decision": "accept",
    "projection": {},
}
_EVIDENCE = {
    "status": "ok",
    "code": "parity-code",
    "code_id": "code-1",
    "definition": "the parity code",
    "version_id": CV,
    "n_units": 1,
    "tabs": {"explicit": [], "question_dependent": []},
}
_UNCODED = {"status": "ok", "version_id": CV, "units": [], "n_uncoded": 0}
_OVERLAP = {
    "status": "ok",
    "version_id": CV,
    "tau_overlap": 0.8,
    "matrix": [],
    "flagged_pairs": [],
    "borderline_units": [],
}
_CYCLE = {
    "status": "completed",
    "exit_code": 0,
    "gate": None,
    "checkpoint_kind": "final",
    "resume_hint": "",
}


# ---------------------------------------------------------------------------
# Shared-leg implementations (the ONE function each surface must route via)
# ---------------------------------------------------------------------------


async def _shared_ingest_text(ctx: dict[str, Any]) -> dict[str, Any]:
    return await ingest_common_module.ingest_text(
        TEXT_BODY,
        ctx["pool"],
        source_type="text",
        config=ctx["config"],
        source="parity-seed",
    )


async def _shared_ingest_file(ctx: dict[str, Any]) -> dict[str, Any]:
    from corpus_kb.tools.ingest_tools import ingest_file

    return await ingest_file(str(ctx["file_path"]), ctx["pool"], config=ctx["config"])


async def _shared_ingest_directory(ctx: dict[str, Any]) -> dict[str, Any]:
    from corpus_kb.tools.ingest_tools import ingest_directory

    return await ingest_directory(str(ctx[_DIR_PATH]), ctx["pool"], config=ctx["config"])


async def _shared_delete_document(ctx: dict[str, Any]) -> dict[str, Any]:
    from corpus_kb.tools.ingest_tools import delete_document

    return await delete_document(DOC_ID, ctx["pool"], config=ctx["config"])


async def _shared_ingest_url(ctx: dict[str, Any]) -> dict[str, Any]:
    from corpus_kb.tools.ingest_url_tools import ingest_url

    return await ingest_url("https://parity.example.com", ctx["pool"], config=ctx["config"])


async def _shared_ingest_status(ctx: dict[str, Any]) -> dict[str, Any]:
    return await ingest_surface_tools_module.ingest_status(ctx["pool"], limit=5)


async def _shared_ingest_reembed(ctx: dict[str, Any]) -> dict[str, Any]:
    return await ingest_surface_tools_module.ingest_reembed(ctx["pool"], config=ctx["config"])


async def _shared_topics_build(ctx: dict[str, Any]) -> dict[str, Any]:
    return await topics_tools_module.topics_build(ctx["pool"], config=ctx["config"])


async def _shared_topics_list(ctx: dict[str, Any]) -> dict[str, Any]:
    return await topics_tools_module.topics_list(ctx["pool"], config=ctx["config"], limit=50)


# -- Analysis program Wave 0 (stub handlers: the disabled envelope is the
#    shared contract every surface answers, exactly like the topics ops) --


async def _shared_topics_trends(ctx: dict[str, Any]) -> dict[str, Any]:
    return await topics_tools_module.topics_trends(ctx["pool"], config=ctx["config"])


async def _shared_labeling_build(ctx: dict[str, Any]) -> dict[str, Any]:
    return await labeling_tools_module.labeling_build(ctx["pool"], config=ctx["config"])


async def _shared_labeling_list(ctx: dict[str, Any]) -> dict[str, Any]:
    return await labeling_tools_module.labeling_list(ctx["pool"], config=ctx["config"], limit=50)


async def _shared_labeling_lfs(ctx: dict[str, Any]) -> dict[str, Any]:
    return await labeling_tools_module.labeling_lfs(ctx["pool"], config=ctx["config"], limit=50)


async def _shared_classification_build(ctx: dict[str, Any]) -> dict[str, Any]:
    return await classification_tools_module.classification_build(ctx["pool"], config=ctx["config"])


async def _shared_classification_list(ctx: dict[str, Any]) -> dict[str, Any]:
    return await classification_tools_module.classification_list(
        ctx["pool"], config=ctx["config"], limit=50
    )


async def _shared_analysis_backfill_dates(ctx: dict[str, Any]) -> dict[str, Any]:
    return await analysis_tools_module.analysis_backfill_dates(ctx["pool"], config=ctx["config"])


async def _shared_analyze_run(ctx: dict[str, Any]) -> dict[str, Any]:
    return await analysis_tools_module.analyze_run(ctx["pool"], config=ctx["config"])


# -- AMR program wave 2 (real handlers; the stubbed world answers on each
#    op's honest guard path, or through the module's own test seams) ------


async def _shared_analysis_select_method(ctx: dict[str, Any]) -> dict[str, Any]:
    return await method_select_tools_module.analysis_select_method(
        ctx["pool"],
        config=ctx["config"],
        task_description="Rank methods for labeling interview themes.",
        output_family="labels",
        inputs_available=["embeddings", "labels"],
        max_complexity_tier="standard",
    )


async def _shared_keyphrases_extract(ctx: dict[str, Any]) -> dict[str, Any]:
    return await keyphrase_tools_module.keyphrases_extract(ctx["pool"], config=ctx["config"])


async def _shared_statements_list(ctx: dict[str, Any]) -> dict[str, Any]:
    return await statements_tools_module.statements_list(ctx["pool"], config=ctx["config"])


async def _shared_statements_timeline(ctx: dict[str, Any]) -> dict[str, Any]:
    return await statements_tools_module.statements_timeline(ctx["pool"], config=ctx["config"])


async def _shared_conflicts_scan(ctx: dict[str, Any]) -> dict[str, Any]:
    return await conflicts_tools_module.conflicts_scan(ctx["pool"], config=ctx["config"])


async def _shared_conflicts_list(ctx: dict[str, Any]) -> dict[str, Any]:
    return await conflicts_tools_module.conflicts_list(ctx["pool"], config=ctx["config"])


_SEED_TENANT = "00000000-0000-0000-0000-000000000001"


async def _seed_one_conflict(ctx: dict[str, Any]) -> str:
    """Seed doc + chunk + two statements + one flag; return the flag id.

    Idempotent on the deterministic hashes: re-runs reuse the rows.
    """
    pool = ctx["pool"]
    async with pool.acquire() as conn:
        await conn.execute("SELECT set_config('app.current_tenant_id', $1, true)", _SEED_TENANT)
        await conn.execute(
            "INSERT INTO documents (tenant_id, source, source_type)"
            " VALUES ($1, 'parity-conflicts-seed', 'text')"
            " ON CONFLICT (tenant_id, source) DO NOTHING",
            _SEED_TENANT,
        )
        doc_id = await conn.fetchval(
            "SELECT doc_id::text FROM documents"
            " WHERE tenant_id = $1 AND source = 'parity-conflicts-seed'",
            _SEED_TENANT,
        )
        await conn.execute(
            "INSERT INTO chunks (tenant_id, doc_id, chunk_index, text, source_type)"
            " VALUES ($1, $2, 0, 'I like ice cream. I dislike ice cream now.', 'text')"
            " ON CONFLICT (tenant_id, doc_id, chunk_index) DO NOTHING",
            _SEED_TENANT,
            doc_id,
        )
        chunk_id = await conn.fetchval(
            "SELECT chunk_id::text FROM chunks"
            " WHERE tenant_id = $1 AND doc_id = $2 AND chunk_index = 0",
            _SEED_TENANT,
            doc_id,
        )
        for seed_text in ("I like ice cream.", "I dislike ice cream now."):
            seed_hash = hashlib.sha256(seed_text.encode("utf-8")).hexdigest()
            await conn.execute(
                "INSERT INTO statements (tenant_id, chunk_id, document_id, statement_hash,"
                " text, template_id, template_version, entities)"
                " VALUES ($1, $2, $3, $4, $5, 'preference', 'v1', '[]'::jsonb)"
                " ON CONFLICT (tenant_id, statement_hash, chunk_id) DO NOTHING",
                _SEED_TENANT,
                chunk_id,
                doc_id,
                seed_hash,
                seed_text,
            )
        stmt_a = await conn.fetchval(
            "SELECT statement_id::text FROM statements"
            " WHERE tenant_id = $1 AND text = 'I like ice cream.' LIMIT 1",
            _SEED_TENANT,
        )
        stmt_b = await conn.fetchval(
            "SELECT statement_id::text FROM statements"
            " WHERE tenant_id = $1 AND text = 'I dislike ice cream now.' LIMIT 1",
            _SEED_TENANT,
        )
        await conn.execute(
            "INSERT INTO statement_conflicts (tenant_id, statement_a_id, statement_b_id,"
            " anchor_name, kind, gate_reached, verdict)"
            " VALUES ($1, $2::uuid, $3::uuid, 'ice cream', 'relation_swap', 'B', 'conflict')"
            " ON CONFLICT (tenant_id, statement_a_id, statement_b_id, kind) DO NOTHING",
            _SEED_TENANT,
            stmt_a,
            stmt_b,
        )
        conflict_id = await conn.fetchval(
            "SELECT conflict_id::text FROM statement_conflicts"
            " WHERE tenant_id = $1 AND anchor_name = 'ice cream'"
            " ORDER BY created_at DESC LIMIT 1",
            _SEED_TENANT,
        )
    ctx["parity_conflict_id"] = str(conflict_id)
    return str(conflict_id)


async def _shared_conflicts_resolve(ctx: dict[str, Any]) -> dict[str, Any]:
    conflict_id = await _seed_one_conflict(ctx)
    return await conflicts_tools_module.conflicts_resolve(
        ctx["pool"],
        config=ctx["config"],
        conflict_id=conflict_id,
        resolved_verdict="dismissed",
        resolved_by="parity-leg",
    )


_SEED_BELIEF = {"subject": "speaker:777", "relation": "preference", "object": "ice cream"}


async def _seed_one_belief(ctx: dict[str, Any]) -> dict[str, str]:
    """Seed two same-speaker statements (like at T-10y, tired at T-0) and
    rebuild beliefs; returns the belief triple for the surface legs."""
    from datetime import UTC, datetime, timedelta

    from corpus_kb.beliefs.store import BeliefStore

    pool = ctx["pool"]
    async with pool.acquire() as conn:
        await conn.execute("SELECT set_config('app.current_tenant_id', $1, true)", _SEED_TENANT)
        await conn.execute(
            "INSERT INTO documents (tenant_id, source, source_type)"
            " VALUES ($1, 'parity-beliefs-seed', 'text')"
            " ON CONFLICT (tenant_id, source) DO NOTHING",
            _SEED_TENANT,
        )
        doc_id = await conn.fetchval(
            "SELECT doc_id::text FROM documents"
            " WHERE tenant_id = $1 AND source = 'parity-beliefs-seed'",
            _SEED_TENANT,
        )
        await conn.execute(
            "INSERT INTO chunks (tenant_id, doc_id, chunk_index, text, source_type)"
            " VALUES ($1, $2, 0, 'Belief seed chunk.', 'text')"
            " ON CONFLICT (tenant_id, doc_id, chunk_index) DO NOTHING",
            _SEED_TENANT,
            doc_id,
        )
        chunk_id = await conn.fetchval(
            "SELECT chunk_id::text FROM chunks"
            " WHERE tenant_id = $1 AND doc_id = $2 AND chunk_index = 0",
            _SEED_TENANT,
            doc_id,
        )
        now = datetime.now(UTC)
        rows = [
            (
                hashlib.sha256(b"belief-seed-old").hexdigest(),
                "I like ice cream.",
                0.9,
                now - timedelta(days=3650),
            ),
            (
                hashlib.sha256(b"belief-seed-new").hexdigest(),
                "I dislike ice cream now.",
                0.9,
                now,
            ),
        ]
        for seed_hash, seed_text, confidence, observed in rows:
            await conn.execute(
                "INSERT INTO statements (tenant_id, chunk_id, document_id, statement_hash,"
                " text, template_id, template_version, entities, exchange_id, confidence,"
                " observed_at)"
                " VALUES ($1, $2, $3, $4, $5, 'preference', 'v1', $6::jsonb, 777, $7, $8)"
                " ON CONFLICT (tenant_id, statement_hash, chunk_id) DO NOTHING",
                _SEED_TENANT,
                chunk_id,
                doc_id,
                seed_hash,
                seed_text,
                '[{"name": "ice cream", "entity_type": "CONCEPT"}]',
                confidence,
                observed,
            )
    store = BeliefStore(pool, _SEED_TENANT)
    await store.rebuild()
    return dict(_SEED_BELIEF)


async def _shared_beliefs_current(ctx: dict[str, Any]) -> dict[str, Any]:
    return await beliefs_tools_module.beliefs_current(ctx["pool"], config=ctx["config"])


async def _shared_beliefs_timeline(ctx: dict[str, Any]) -> dict[str, Any]:
    triple = await _seed_one_belief(ctx)
    return await beliefs_tools_module.beliefs_timeline(ctx["pool"], config=ctx["config"], **triple)


async def _shared_beliefs_predict(ctx: dict[str, Any]) -> dict[str, Any]:
    triple = await _seed_one_belief(ctx)
    return await beliefs_tools_module.beliefs_predict(ctx["pool"], config=ctx["config"], **triple)


async def _shared_beliefs_compare(ctx: dict[str, Any]) -> dict[str, Any]:
    triple = await _seed_one_belief(ctx)
    return await beliefs_tools_module.beliefs_compare(
        ctx["pool"], config=ctx["config"], subject=triple["subject"]
    )


async def _shared_extraction_tune(ctx: dict[str, Any]) -> dict[str, Any]:
    # dry_run: the parity world has no filesystem contract; the envelope
    # shape (and the canned mine row) are what the legs pin.
    return await extraction_tune_tools_module.extraction_tune(
        ctx["pool"], config=ctx["config"], dry_run=True
    )


async def _shared_analysis_embed_meta(ctx: dict[str, Any]) -> dict[str, Any]:
    # document kind only: the profile kind would call the real embedder
    # (env-dependent); the canned conn answers the mapping reads empty.
    return await meta_embed_tools_module.analysis_embed_meta(
        ctx["pool"], config=ctx["config"], kinds="document"
    )


async def _shared_lexical_classify(ctx: dict[str, Any]) -> dict[str, Any]:
    return await lexical_tools_module.lexical_classify(ctx["pool"], config=ctx["config"])


async def _shared_neighbors_classify(ctx: dict[str, Any]) -> dict[str, Any]:
    return await neighbor_tools_module.neighbors_classify(ctx["pool"], config=ctx["config"])


async def _shared_centroids_classify(ctx: dict[str, Any]) -> dict[str, Any]:
    return await centroid_tools_module.centroids_classify(ctx["pool"], config=ctx["config"])


async def _shared_clusters_inspect(ctx: dict[str, Any]) -> dict[str, Any]:
    return await cluster_tools_module.clusters_inspect(ctx["pool"], config=ctx["config"])


async def _shared_duplicates_screen(ctx: dict[str, Any]) -> dict[str, Any]:
    return await vector_screen_tools_module.duplicates_screen(ctx["pool"], config=ctx["config"])


async def _shared_outliers_rank(ctx: dict[str, Any]) -> dict[str, Any]:
    # k stays at or below the canned corpus minus two: the canned world is
    # tiny, and the k-th-nearest scan must never meet the self marker.
    return await vector_screen_tools_module.outliers_rank(ctx["pool"], config=ctx["config"], k=2)


async def _shared_nli_classify(ctx: dict[str, Any]) -> dict[str, Any]:
    return await nli_tools_module.nli_classify(
        ctx["pool"], config=ctx["config"], labels=["reports", "meetings"]
    )


async def _shared_contradictions_screen(ctx: dict[str, Any]) -> dict[str, Any]:
    return await nli_tools_module.contradictions_screen(ctx["pool"], config=ctx["config"])


async def _shared_sentiment_screen(ctx: dict[str, Any]) -> dict[str, Any]:
    return await sentiment_tools_module.sentiment_screen(ctx["pool"], config=ctx["config"])


async def _shared_active_learn_next(ctx: dict[str, Any]) -> dict[str, Any]:
    return await active_learn_tools_module.active_learn_next(ctx["pool"], config=ctx["config"])


async def _shared_search(ctx: dict[str, Any]) -> dict[str, Any]:
    return await search_tools_module.search(ctx["pool"], "parity probe", config=ctx["config"], k=3)


async def _shared_ask(ctx: dict[str, Any]) -> dict[str, Any]:
    from corpus_kb.tools.ask_tools import ask as ask_tool

    return await ask_tool("parity question?", ctx["config"], k=3, pg_pool=ctx["pool"])


async def _shared_query(ctx: dict[str, Any]) -> dict[str, Any]:
    result = await router_handler_module.get_router_handler().handle_routed_query(
        RoutedQuery(tenant_id=UUID(TENANT), query="parity probe", k=3)
    )
    return {"status": "success", "result": result.model_dump(mode="json")}


async def _shared_verify(ctx: dict[str, Any]) -> dict[str, Any]:
    from corpus_kb.domain.models import VerifyAnswerQuery

    result = await query_handler_module.get_query_handler().handle_verify_answer(
        VerifyAnswerQuery(answer="parity answer", chunk_ids=[])
    )
    return {"status": "success", "result": result.model_dump(mode="json")}


async def _shared_list_documents(ctx: dict[str, Any]) -> dict[str, Any]:
    return await list_documents_tool(ctx["pool"], ctx["config"])


async def _shared_add_entity(ctx: dict[str, Any]) -> dict[str, Any]:
    from corpus_kb.domain.models import AddEntityCommand

    return command_handler_module.get_command_handler().handle_add_entity(
        AddEntityCommand(
            tenant_id=UUID(TENANT), name="parity-entity", entity_type="concept", metadata={}
        )
    )


async def _shared_add_relation(ctx: dict[str, Any]) -> dict[str, Any]:
    from corpus_kb.domain.models import AddRelationCommand

    return command_handler_module.get_command_handler().handle_add_relation(
        AddRelationCommand(
            tenant_id=UUID(TENANT),
            source_entity_id=UUID(ENTITY_A),
            target_entity_id=UUID(ENTITY_B),
            relation_type="related_to",
            weight=1.0,
            metadata={},
        )
    )


async def _shared_search_graph(ctx: dict[str, Any]) -> dict[str, Any]:
    from corpus_kb.tools.graph_read_tools import search_graph

    return await search_graph(ctx["pool"], "parity", config=ctx["config"])


async def _shared_get_entity_relations(ctx: dict[str, Any]) -> dict[str, Any]:
    from corpus_kb.tools.graph_read_tools import get_entity_relations

    return await get_entity_relations(ctx["pool"], ENTITY_A, config=ctx["config"])


async def _shared_graph_neighbors(ctx: dict[str, Any]) -> dict[str, Any]:
    from corpus_kb.tools.graph_read_tools import graph_neighbors

    return await graph_neighbors(ctx["pool"], ENTITY_A, hops=2, config=ctx["config"])


async def _shared_graph_traverse(ctx: dict[str, Any]) -> dict[str, Any]:
    from corpus_kb.tools.graph_read_tools import graph_traverse

    return await graph_traverse(ctx["pool"], ENTITY_A, max_depth=2, config=ctx["config"])


def _release_leg(
    fn_name: str, http: str, body: dict[str, Any], mcp_args: dict[str, Any], **tool_kwargs: Any
) -> Any:
    """Shared + HTTP + socket + MCP legs for one release op."""
    fn: Callable[..., Any] = getattr(release_tools_module, fn_name)

    async def shared(ctx: dict[str, Any]) -> dict[str, Any]:
        return await fn(ctx["pool"], **tool_kwargs)

    def http_spec(ctx: dict[str, Any]) -> dict[str, Any]:
        return {"method": "post", "path": http, "json": dict(body)}

    return {
        "shared": shared,
        "http": http_spec,
        "socket": _sock(fn_name, dict(body)),
        "mcp": _mcp(mcp_args),
    }


async def _shared_release_compare(ctx: dict[str, Any]) -> dict[str, Any]:
    return await release_tools_module.release_compare(CV, RELEASE_ID, RELEASE_ID)


async def _shared_transcript(ctx: dict[str, Any]) -> dict[str, Any]:
    return await research_tools_module.research_ingest_transcript("interview.txt", ctx["pool"])


async def _shared_notebook_ask(ctx: dict[str, Any]) -> dict[str, Any]:
    return await notebook_tools_module.notebook_ask_tool(
        "parity question?", ctx["pool"], PROJECT_ID, retrieval_only=True
    )


# -- Promoted research ops: the shared tool layer is the ONE function ------


async def _shared_research_ingest(ctx: dict[str, Any]) -> dict[str, Any]:
    return await research_tools_module.research_ingest("interview.txt", ctx["pool"])


async def _shared_coding_run(ctx: dict[str, Any]) -> dict[str, Any]:
    return await research_tools_module.coding_run(ctx["pool"], CV)


async def _shared_inductive_run(ctx: dict[str, Any]) -> dict[str, Any]:
    return await research_tools_module.inductive_run(ctx["pool"])


async def _shared_codebook_promote(ctx: dict[str, Any]) -> dict[str, Any]:
    return await research_tools_module.codebook_promote(ctx["pool"], 1, "n", "d")


async def _shared_research_report(ctx: dict[str, Any]) -> dict[str, Any]:
    return await research_tools_module.research_report(ctx["pool"], level="novice")


async def _shared_review_execute(ctx: dict[str, Any]) -> dict[str, Any]:
    return await research_tools_module.review_execute(
        ctx["pool"], ASSIGNMENT_ID, "accept", "parity"
    )


async def _shared_notebook_evidence(ctx: dict[str, Any]) -> dict[str, Any]:
    return await notebook_tools_module.notebook_evidence_tool("parity-code", ctx["pool"])


async def _shared_notebook_uncoded(ctx: dict[str, Any]) -> dict[str, Any]:
    return await notebook_tools_module.notebook_uncoded_tool(ctx["pool"])


async def _shared_notebook_overlap(ctx: dict[str, Any]) -> dict[str, Any]:
    return await notebook_tools_module.notebook_overlap_tool(ctx["pool"])


async def _shared_research_cycle(ctx: dict[str, Any]) -> dict[str, Any]:
    from corpus_kb.tools.cycle_tools import research_cycle

    return await research_cycle(ctx["pool"], mode="in")


async def _shared_research_demo(ctx: dict[str, Any]) -> dict[str, Any]:
    from corpus_kb.tools.demo_tools import research_demo

    return await research_demo()


def _post(path: str, body: dict[str, Any]) -> Any:
    def spec(ctx: dict[str, Any]) -> dict[str, Any]:
        return {"method": "post", "path": path, "json": dict(body)}

    return spec


def _get(path: str, params: dict[str, Any]) -> Any:
    def spec(ctx: dict[str, Any]) -> dict[str, Any]:
        return {"method": "get", "path": path, "params": dict(params)}

    return spec


@dataclass(frozen=True)
class _Leg:
    """The invocation plan for one catalogue operation."""

    shared: Callable[[dict[str, Any]], Awaitable[dict[str, Any]]]
    http: Callable[[dict[str, Any]], dict[str, Any]]
    socket: Callable[[dict[str, Any]], tuple[str, dict[str, Any]]] | None = None
    # The MCP tool arguments (ops with an MCP surface only; the catalogue
    # registers those tools under the operation's own name).
    mcp: Callable[[dict[str, Any]], dict[str, Any]] | None = None


def _sock(method: str, params: dict[str, Any]) -> Any:
    def spec(ctx: dict[str, Any]) -> tuple[str, dict[str, Any]]:
        return method, dict(params)

    return spec


def _mcp(args: dict[str, Any]) -> Any:
    def spec(ctx: dict[str, Any]) -> dict[str, Any]:
        return dict(args)

    return spec


_FILE_PATH = "file_path"
_DIR_PATH = "dir_path"
DOC_ID = "00000000-0000-0000-0000-00000000000d"

LEGS: dict[str, _Leg] = {
    "ingest_text": _Leg(
        _shared_ingest_text,
        _post(
            "/api/ingest/text",
            {"text": TEXT_BODY, "source": "parity-seed", "source_type": "text"},
        ),
        _sock("ingest_text", {"text": TEXT_BODY, "source": "parity-seed"}),
        _mcp({"text": TEXT_BODY, "source": "parity-seed", "source_type": "text"}),
    ),
    "ingest_file": _Leg(
        _shared_ingest_file,
        lambda ctx: {
            "method": "post",
            "path": "/api/ingest/file",
            "json": {"file_path": str(ctx[_FILE_PATH])},
        },
        lambda ctx: ("ingest_file", {"file_path": str(ctx[_FILE_PATH])}),
        lambda ctx: {"file_path": str(ctx[_FILE_PATH])},
    ),
    "ingest_directory": _Leg(
        lambda ctx: _shared_ingest_directory(ctx),
        lambda ctx: {
            "method": "post",
            "path": "/api/ingest/directory",
            "json": {"directory_path": str(ctx[_DIR_PATH])},
        },
        lambda ctx: ("ingest_directory", {"directory_path": str(ctx[_DIR_PATH])}),
        lambda ctx: {"directory_path": str(ctx[_DIR_PATH])},
    ),
    "ingest_url": _Leg(
        _shared_ingest_url,
        _post("/api/ingest/url", {"url": "https://parity.example.com"}),
        _sock("ingest_url", {"url": "https://parity.example.com"}),
        _mcp({"url": "https://parity.example.com"}),
    ),
    "ingest_status": _Leg(
        _shared_ingest_status,
        _get("/api/ingest/status", {"limit": 5}),
        _sock("ingest_status", {"limit": 5}),
        mcp=_mcp({"limit": 5}),
    ),
    "ingest_reembed": _Leg(
        _shared_ingest_reembed,
        _post("/api/ingest/reembed", {}),
        _sock("ingest_reembed", {}),
        mcp=_mcp({}),
    ),
    "topics_build": _Leg(
        _shared_topics_build,
        _post("/api/topics/build", {}),
        _sock("topics_build", {}),
        _mcp({}),
    ),
    "topics_list": _Leg(
        _shared_topics_list,
        _get("/api/topics", {"limit": 50}),
        _sock("topics_list", {"limit": 50}),
        _mcp({"limit": 50}),
    ),
    # -- analysis program Wave 0 (stub handlers, disabled path) --------------
    "topics_trends": _Leg(
        _shared_topics_trends,
        _post("/api/topics/trends", {}),
        _sock("topics_trends", {}),
        _mcp({}),
    ),
    "labeling_build": _Leg(
        _shared_labeling_build,
        _post("/api/labeling/build", {}),
        _sock("labeling_build", {}),
        _mcp({}),
    ),
    "labeling_list": _Leg(
        _shared_labeling_list,
        _get("/api/labeling", {"limit": 50}),
        _sock("labeling_list", {"limit": 50}),
        _mcp({"limit": 50}),
    ),
    "labeling_lfs": _Leg(
        _shared_labeling_lfs,
        _get("/api/labeling/lfs", {"limit": 50}),
        _sock("labeling_lfs", {"limit": 50}),
        _mcp({"limit": 50}),
    ),
    "classification_build": _Leg(
        _shared_classification_build,
        _post("/api/classification/build", {}),
        _sock("classification_build", {}),
        _mcp({}),
    ),
    "classification_list": _Leg(
        _shared_classification_list,
        _get("/api/classification", {"limit": 50}),
        _sock("classification_list", {"limit": 50}),
        _mcp({"limit": 50}),
    ),
    "analysis_backfill_dates": _Leg(
        _shared_analysis_backfill_dates,
        _post("/api/analysis/backfill-dates", {}),
        _sock("analysis_backfill_dates", {}),
        _mcp({}),
    ),
    "analyze_run": _Leg(
        _shared_analyze_run,
        _post("/api/analyze", {}),
        _sock("analyze_run", {}),
        _mcp({}),
    ),
    # -- AMR program wave 2 (method ops over the same stubbed world) ---------
    "analysis_select_method": _Leg(
        _shared_analysis_select_method,
        _post(
            "/api/analysis/select-method",
            {
                "task_description": "Rank methods for labeling interview themes.",
                "output_family": "labels",
                "inputs_available": ["embeddings", "labels"],
                "max_complexity_tier": "standard",
            },
        ),
        _sock(
            "analysis_select_method",
            {
                "task_description": "Rank methods for labeling interview themes.",
                "output_family": "labels",
                "inputs_available": ["embeddings", "labels"],
                "max_complexity_tier": "standard",
            },
        ),
        _mcp(
            {
                "task_description": "Rank methods for labeling interview themes.",
                "output_family": "labels",
                "inputs_available": ["embeddings", "labels"],
                "max_complexity_tier": "standard",
            }
        ),
    ),
    "keyphrases_extract": _Leg(
        _shared_keyphrases_extract,
        _post("/api/analysis/keyphrases-extract", {}),
        _sock("keyphrases_extract", {}),
        _mcp({}),
    ),
    # Living Corpus W2: the statement-atom read ops.
    "statements_list": _Leg(
        _shared_statements_list,
        _post("/api/statements/list", {}),
        _sock("statements_list", {}),
        _mcp({}),
    ),
    "statements_timeline": _Leg(
        _shared_statements_timeline,
        _post("/api/statements/timeline", {}),
        _sock("statements_timeline", {}),
        _mcp({}),
    ),
    # -- Living Corpus W3: the conflict ops. The resolve leg seeds its own
    #    minimal flag world and resolves it as dismissed (idempotent).
    "conflicts_scan": _Leg(
        _shared_conflicts_scan,
        _post("/api/conflicts/scan", {}),
        _sock("conflicts_scan", {}),
        _mcp({}),
    ),
    "conflicts_list": _Leg(
        _shared_conflicts_list,
        _post("/api/conflicts/list", {}),
        _sock("conflicts_list", {}),
        _mcp({}),
    ),
    "conflicts_resolve": _Leg(
        _shared_conflicts_resolve,
        lambda ctx: {
            "method": "post",
            "path": "/api/conflicts/resolve",
            "json": {
                "conflict_id": ctx.get("parity_conflict_id", ""),
                "resolved_verdict": "dismissed",
                "resolved_by": "parity-leg",
            },
        },
        lambda ctx: (
            "conflicts_resolve",
            {
                "conflict_id": ctx.get("parity_conflict_id", ""),
                "resolved_verdict": "dismissed",
                "resolved_by": "parity-leg",
            },
        ),
        lambda ctx: {
            "conflict_id": ctx.get("parity_conflict_id", ""),
            "resolved_verdict": "dismissed",
            "resolved_by": "parity-leg",
        },
    ),
    # -- Living Corpus W4: the belief ops. The timeline/predict/compare
    #    legs seed one like->dislike arc and rebuild before reading.
    "beliefs_current": _Leg(
        _shared_beliefs_current,
        _post("/api/beliefs/current", {}),
        _sock("beliefs_current", {}),
        _mcp({}),
    ),
    # -- Living Corpus W5: the tune op (dry-run legs; no filesystem side
    #    effects in the parity world).
    "extraction_tune": _Leg(
        _shared_extraction_tune,
        _post("/api/extraction-tune", {"dry_run": True}),
        _sock("extraction_tune", {"dry_run": True}),
        _mcp({"dry_run": True}),
    ),
    # -- Living Corpus W6: the meta-embed build (document kind only; the
    #    profile kind would call the real embedder — env-dependent).
    "analysis_embed_meta": _Leg(
        _shared_analysis_embed_meta,
        _post("/api/analysis/embed-meta", {"kinds": "document"}),
        _sock("analysis_embed_meta", {"kinds": "document"}),
        _mcp({"kinds": "document"}),
    ),
    "beliefs_timeline": _Leg(
        _shared_beliefs_timeline,
        lambda ctx: {
            "method": "post",
            "path": "/api/beliefs/timeline",
            "json": dict(ctx.get("parity_belief_seed", _SEED_BELIEF)),
        },
        lambda ctx: ("beliefs_timeline", dict(ctx.get("parity_belief_seed", _SEED_BELIEF))),
        lambda ctx: dict(ctx.get("parity_belief_seed", _SEED_BELIEF)),
    ),
    "beliefs_predict": _Leg(
        _shared_beliefs_predict,
        lambda ctx: {
            "method": "post",
            "path": "/api/beliefs/predict",
            "json": dict(ctx.get("parity_belief_seed", _SEED_BELIEF)),
        },
        lambda ctx: ("beliefs_predict", dict(ctx.get("parity_belief_seed", _SEED_BELIEF))),
        lambda ctx: dict(ctx.get("parity_belief_seed", _SEED_BELIEF)),
    ),
    "beliefs_compare": _Leg(
        _shared_beliefs_compare,
        lambda ctx: {
            "method": "post",
            "path": "/api/beliefs/compare",
            "json": {"subject": _SEED_BELIEF["subject"]},
        },
        lambda ctx: ("beliefs_compare", {"subject": _SEED_BELIEF["subject"]}),
        lambda ctx: {"subject": _SEED_BELIEF["subject"]},
    ),
    "lexical_classify": _Leg(
        _shared_lexical_classify,
        _post("/api/analysis/lexical-classify", {}),
        _sock("lexical_classify", {}),
        _mcp({}),
    ),
    "neighbors_classify": _Leg(
        _shared_neighbors_classify,
        _post("/api/analysis/neighbors-classify", {}),
        _sock("neighbors_classify", {}),
        _mcp({}),
    ),
    "centroids_classify": _Leg(
        _shared_centroids_classify,
        _post("/api/analysis/centroids-classify", {}),
        _sock("centroids_classify", {}),
        _mcp({}),
    ),
    "clusters_inspect": _Leg(
        _shared_clusters_inspect,
        _post("/api/analysis/clusters-inspect", {}),
        _sock("clusters_inspect", {}),
        _mcp({}),
    ),
    "duplicates_screen": _Leg(
        _shared_duplicates_screen,
        _post("/api/analysis/duplicates-screen", {}),
        _sock("duplicates_screen", {}),
        _mcp({}),
    ),
    "outliers_rank": _Leg(
        _shared_outliers_rank,
        _post("/api/analysis/outliers-rank", {"k": 2}),
        _sock("outliers_rank", {"k": 2}),
        _mcp({"k": 2}),
    ),
    "nli_classify": _Leg(
        _shared_nli_classify,
        _post("/api/analysis/nli-classify", {"labels": ["reports", "meetings"]}),
        _sock("nli_classify", {"labels": ["reports", "meetings"]}),
        _mcp({"labels": ["reports", "meetings"]}),
    ),
    "contradictions_screen": _Leg(
        _shared_contradictions_screen,
        _post("/api/analysis/contradictions-screen", {}),
        _sock("contradictions_screen", {}),
        _mcp({}),
    ),
    "sentiment_screen": _Leg(
        _shared_sentiment_screen,
        _post("/api/analysis/sentiment-screen", {}),
        _sock("sentiment_screen", {}),
        _mcp({}),
    ),
    "active_learn_next": _Leg(
        _shared_active_learn_next,
        _post("/api/analysis/active-learn-next", {}),
        _sock("active_learn_next", {}),
        _mcp({}),
    ),
    "search": _Leg(
        _shared_search,
        _post("/api/search", {"query": "parity probe", "k": 3}),
        _sock("search", {"query": "parity probe", "k": 3}),
        _mcp({"query": "parity probe", "k": 3}),
    ),
    "ask": _Leg(
        _shared_ask,
        _post("/api/ask", {"query": "parity question?", "k": 3}),
        _sock("ask", {"query": "parity question?", "k": 3}),
        _mcp({"query": "parity question?", "k": 3}),
    ),
    "query": _Leg(
        _shared_query,
        _post("/api/query", {"query": "parity probe", "k": 3}),
        _sock("query", {"query": "parity probe", "k": 3}),
        mcp=_mcp({"query": "parity probe", "k": 3}),
    ),
    "verify_answer": _Leg(
        _shared_verify,
        _post("/api/verify", {"answer": "parity answer", "chunk_ids": []}),
        _sock("verify_answer", {"answer": "parity answer", "chunk_ids": []}),
        mcp=_mcp({"answer": "parity answer", "chunk_ids": []}),
    ),
    "list_documents": _Leg(
        _shared_list_documents,
        _get("/api/documents", {"limit": 5, "offset": 0}),
        _sock("list_documents", {"limit": 5, "offset": 0}),
        _mcp({"limit": 5, "offset": 0}),
    ),
    "delete_document": _Leg(
        lambda ctx: _shared_delete_document(ctx),
        lambda ctx: {"method": "delete", "path": f"/api/documents/{DOC_ID}"},
        _sock("delete_document", {"document_id": DOC_ID}),
        mcp=_mcp({"document_id": DOC_ID}),
    ),
    "add_entity": _Leg(
        _shared_add_entity,
        _post("/api/entities", {"name": "parity-entity", "entity_type": "concept"}),
        _sock("add_entity", {"name": "parity-entity", "entity_type": "concept"}),
        mcp=_mcp({"name": "parity-entity", "entity_type": "concept"}),
    ),
    "add_relation": _Leg(
        _shared_add_relation,
        _post(
            "/api/relations",
            {
                "source_entity_id": ENTITY_A,
                "target_entity_id": ENTITY_B,
                "relation_type": "related_to",
                "weight": 1.0,
            },
        ),
        _sock(
            "add_relation",
            {
                "source_entity_id": ENTITY_A,
                "target_entity_id": ENTITY_B,
                "relation_type": "related_to",
                "weight": 1.0,
            },
        ),
        mcp=_mcp(
            {
                "source_entity_id": ENTITY_A,
                "target_entity_id": ENTITY_B,
                "relation_type": "related_to",
                "weight": 1.0,
            }
        ),
    ),
    "search_graph": _Leg(
        _shared_search_graph,
        _post("/api/graph/search", {"query": "parity"}),
        _sock("search_graph", {"query": "parity"}),
        _mcp({"query": "parity"}),
    ),
    "get_entity_relations": _Leg(
        _shared_get_entity_relations,
        _get(f"/api/graph/relations/{ENTITY_A}", {"limit": "50"}),
        _sock("get_entity_relations", {"entity_id": ENTITY_A, "limit": 50}),
        _mcp({"entity_id": ENTITY_A, "limit": 50}),
    ),
    "graph_neighbors": _Leg(
        _shared_graph_neighbors,
        _post(
            "/api/graph/neighbors",
            {"entity_id": ENTITY_A, "hops": 2, "direction": "out"},
        ),
        _sock("graph_neighbors", {"entity_id": ENTITY_A, "hops": 2, "direction": "out"}),
        _mcp({"entity_id": ENTITY_A, "hops": 2, "direction": "out"}),
    ),
    "graph_traverse": _Leg(
        _shared_graph_traverse,
        _post("/api/graph/traverse", {"entity_id": ENTITY_A, "max_depth": 2}),
        _sock("graph_traverse", {"entity_id": ENTITY_A, "max_depth": 2}),
        _mcp({"entity_id": ENTITY_A, "max_depth": 2}),
    ),
    "release_assess": _Leg(
        **_release_leg(
            "release_assess",
            "/api/release/assess",
            {"codebook_version_id": CV},
            {"codebook_version_id": CV},
            codebook_version_id=CV,
        )
    ),
    "release_request": _Leg(
        **_release_leg(
            "release_request",
            "/api/release/request",
            {"codebook_version_id": CV, "creator": "parity"},
            {"codebook_version_id": CV, "creator": "parity"},
            codebook_version_id=CV,
            creator="parity",
        )
    ),
    "release_review": _Leg(
        **_release_leg(
            "release_review",
            "/api/release/review",
            {"codebook_version_id": CV, "release_id": RELEASE_ID},
            {"codebook_version_id": CV, "release_id": RELEASE_ID},
            codebook_version_id=CV,
            release_id=RELEASE_ID,
        )
    ),
    "release_execute": _Leg(
        **_release_leg(
            "release_execute",
            "/api/release/release",
            {"codebook_version_id": CV, "release_id": RELEASE_ID, "approver": "parity"},
            {
                "codebook_version_id": CV,
                "release_id": RELEASE_ID,
                "approver": "parity",
                "waivers": [["gate", "parity waiver"]],
            },
            codebook_version_id=CV,
            release_id=RELEASE_ID,
            approver="parity",
            waivers=[["gate", "parity waiver"]],
        )
    ),
    "release_status": _Leg(
        lambda ctx: _shared_release_status(ctx),
        _get("/api/release/status", {"codebook_version_id": CV}),
        _sock("release_status", {"codebook_version_id": CV}),
        mcp=_mcp({"codebook_version_id": CV}),
    ),
    "release_compare": _Leg(
        _shared_release_compare,
        _post(
            "/api/release/compare",
            {"codebook_version_id": CV, "old_release_id": RELEASE_ID, "new_release_id": RELEASE_ID},
        ),
        _sock(
            "release_compare",
            {"codebook_version_id": CV, "old_release_id": RELEASE_ID, "new_release_id": RELEASE_ID},
        ),
        mcp=_mcp(
            {
                "codebook_version_id": CV,
                "old_release_id": RELEASE_ID,
                "new_release_id": RELEASE_ID,
            }
        ),
    ),
    "research_ingest_transcript": _Leg(
        _shared_transcript,
        _post("/api/research/ingest-transcript", {"file_path": "interview.txt"}),
        _sock("research_ingest_transcript", {"file_path": "interview.txt"}),
        mcp=_mcp({"file_path": "interview.txt"}),
    ),
    "notebook_ask": _Leg(
        _shared_notebook_ask,
        _post(
            "/api/notebook/ask",
            {"project_id": PROJECT_ID, "question": "parity question?", "retrieval_only": True},
        ),
        _sock(
            "notebook_ask",
            {"project_id": PROJECT_ID, "question": "parity question?", "retrieval_only": True},
        ),
        mcp=_mcp(
            {"question": "parity question?", "project_id": PROJECT_ID, "retrieval_only": True}
        ),
    ),
    # -- promoted research ops (full surface parity wave) --------------------
    "research_ingest": _Leg(
        _shared_research_ingest,
        _post("/api/research/ingest", {"path": "interview.txt"}),
        _sock("research_ingest", {"path": "interview.txt"}),
        mcp=_mcp({"path": "interview.txt"}),
    ),
    "coding_run": _Leg(
        _shared_coding_run,
        _post("/api/coding/run", {"codebook_version_id": CV}),
        _sock("coding_run", {"codebook_version_id": CV}),
        mcp=_mcp({"codebook_version_id": CV}),
    ),
    "inductive_run": _Leg(
        _shared_inductive_run,
        _post("/api/coding/inductive", {}),
        _sock("inductive_run", {}),
        mcp=_mcp({}),
    ),
    "codebook_promote": _Leg(
        _shared_codebook_promote,
        _post("/api/codebook/promote", {"proposed_id": 1, "name": "n", "definition": "d"}),
        _sock("codebook_promote", {"proposed_id": 1, "name": "n", "definition": "d"}),
        mcp=_mcp({"proposed_id": 1, "name": "n", "definition": "d"}),
    ),
    "research_report": _Leg(
        _shared_research_report,
        _post("/api/research/report", {"level": "novice"}),
        _sock("research_report", {"level": "novice"}),
        mcp=_mcp({"level": "novice"}),
    ),
    "review_execute": _Leg(
        _shared_review_execute,
        _post(
            "/api/review/execute",
            {"assignment_id": ASSIGNMENT_ID, "decision": "accept", "reviewer": "parity"},
        ),
        _sock(
            "review_execute",
            {"assignment_id": ASSIGNMENT_ID, "decision": "accept", "reviewer": "parity"},
        ),
        mcp=_mcp({"assignment_id": ASSIGNMENT_ID, "decision": "accept", "reviewer": "parity"}),
    ),
    "notebook_evidence": _Leg(
        _shared_notebook_evidence,
        _post("/api/notebook/evidence", {"code": "parity-code"}),
        _sock("notebook_evidence", {"code": "parity-code"}),
        mcp=_mcp({"code": "parity-code"}),
    ),
    "notebook_uncoded": _Leg(
        _shared_notebook_uncoded,
        _post("/api/notebook/uncoded", {}),
        _sock("notebook_uncoded", {}),
        mcp=_mcp({}),
    ),
    "notebook_overlap": _Leg(
        _shared_notebook_overlap,
        _post("/api/notebook/overlap", {}),
        _sock("notebook_overlap", {}),
        mcp=_mcp({}),
    ),
    "research_cycle": _Leg(
        _shared_research_cycle,
        _post("/api/research/cycle", {"mode": "in"}),
        _sock("research_cycle", {"mode": "in"}),
        mcp=_mcp({"mode": "in"}),
    ),
    "research_demo": _Leg(
        _shared_research_demo,
        _post("/api/research/demo", {}),
        _sock("research_demo", {}),
        mcp=_mcp({}),
    ),
}


async def _shared_release_status(ctx: dict[str, Any]) -> dict[str, Any]:
    return await release_tools_module.release_status(ctx["pool"], CV)


# ---------------------------------------------------------------------------
# Offline stubs (pool / pipeline store / eventsourcing app / handlers)
# ---------------------------------------------------------------------------


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
        # execute is the tenant-GUC write (always OK); fetchrow is the
        # single-row beliefs posterior probe (the only one on this stub).
        if name == "execute":
            return self._ok
        if name == "fetchrow":
            return self._belief_row
        raise AttributeError(name)

    async def _ok(self, *args: Any) -> str:
        return "OK"

    async def _belief_row(self, *args: Any) -> dict[str, Any]:
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

    async def unchanged_doc_id(self, source: str, file_hash: str) -> str | None:
        return self.unchanged

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
    ) -> dict[str, Any]:
        return dict(_TRANSCRIPT)

    async def ingest_path(
        self,
        tenant_id: UUID,
        path: str,
        project_id: UUID | None = None,
        force: bool = False,
    ) -> dict[str, Any]:
        return dict(_INGEST_PATH)


class _StubLlmHandler:
    def __init__(self, config: Any = None) -> None:
        self.config = config


def _offline_config() -> dict[str, Any]:
    return {
        "graph": {"extract_entities": False},
        "embedding": {"provider": "pgml"},
        "ingest": {"chunking": {"enabled": False}},
    }


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

    monkeypatch.setattr(query_handler_module, "get_query_handler", fake_query_handler)
    monkeypatch.setattr(QueryHandler, "handle_search_with_meta", canned_search)
    monkeypatch.setattr(QueryHandler, "handle_verify_answer", canned_verify)
    monkeypatch.setattr(QueryHandler, "handle_list_documents", canned_list_documents)
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

    # Research transcript + path ingest: stub the handler + the projection
    # catch-up.
    monkeypatch.setattr(
        research_tools_module, "get_research_handler", lambda pool: _StubResearchHandler()
    )

    async def noop_advance(pool: Any, tenant_id: UUID) -> None:
        return None

    monkeypatch.setattr(research_tools_module, "advance_all_projections", noop_advance)

    # Promoted research ops (full surface parity wave): stub at the exact
    # seam the shared tool layer imports at call time.
    monkeypatch.setattr(config_module, "load_config", _offline_config)
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

    async def _canned_verdict(prompt: str, model: str, base_url: str) -> str:
        return "ENTAIL"

    monkeypatch.setattr(nli_contract_module, "judge_verdict", _canned_verdict)

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
        "config": _offline_config(),
        "client": TestClient(create_http_app()),
        "server": JSONRPCServer(),
        "mcp_server": build_mcp_server(pool, _offline_config()),
        _FILE_PATH: file_path,
        _DIR_PATH: tmp_path,
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
    for op in OPERATIONS:
        # Full surface parity: every catalogue operation declares all four
        # surfaces and carries a leg for each one that runs envelopes.
        kinds = {surface.kind for surface in op.surfaces}
        assert kinds == {"cli", "http", "mcp", "socket"}, (
            f"{op.name} must declare all four surfaces (has {sorted(kinds)})"
        )
        assert LEGS[op.name].socket is not None, f"{op.name} needs a socket leg"
        # Every catalogue MCP surface gets an MCP leg (and only those do) —
        # the MCP server is generated from the same rows.
        has_surface = bool(surfaces_of(op, "mcp"))
        has_leg = LEGS[op.name].mcp is not None
        assert has_surface == has_leg, (
            f"{op.name}: MCP surface/leg mismatch (surface={has_surface}, leg={has_leg})"
        )


def test_catalogue_matches_surface_registry() -> None:
    """Rows whose op exists in surface_registry must byte-match its columns."""
    from corpus_kb.surface_registry import SURFACES

    registry = {surface.name.replace("-", "_"): surface for surface in SURFACES}
    checked: list[str] = []
    for op in OPERATIONS:
        surface = registry.get(op.name)
        if surface is None:
            continue
        mcp = surfaces_of(op, "mcp")
        cli = surfaces_of(op, "cli")
        http = surfaces_of(op, HTTP)
        assert mcp and mcp[0].route == surface.mcp_tool, op.name
        assert cli and cli[0].route == surface.cli_path, op.name
        assert http and http[0].route == surface.http_route, op.name
        checked.append(op.name)
    # The check must stay meaningful: these pinned rows prove it ran.
    assert {
        "release_assess",
        "release_status",
        "ingest_status",
        "topics_build",
        "topics_list",
        # Analysis program Wave 0 (the registry skeleton rows).
        "topics_trends",
        "labeling_build",
        "labeling_list",
        "labeling_lfs",
        "classification_build",
        "classification_list",
        "analysis_backfill_dates",
        "analyze_run",
        # Analysis method register program (wave 2 method rows).
        "analysis_select_method",
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
    from corpus_kb.cli_operations import build_inventory

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
