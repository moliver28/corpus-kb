"""Shared parity legs for the deep surface-parity gate (test helper).

The ONE shared function per catalogue operation, the leg specs, and the
LEGS table (moved from tests/test_surface_parity_deep.py, W4-1). A test
helper, NOT a collected module: no test_ prefix. The gate file imports
the names it needs.
"""

from __future__ import annotations

import hashlib
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

import corpus_kb.handlers.command_handler as command_handler_module
import corpus_kb.handlers.query_handler as query_handler_module
import corpus_kb.handlers.router_handler as router_handler_module
import corpus_kb.tools.active_learn_tools as active_learn_tools_module
import corpus_kb.tools.analysis_route_tools as analysis_route_tools_module
import corpus_kb.tools.analysis_tools as analysis_tools_module
import corpus_kb.tools.beliefs_tools as beliefs_tools_module
import corpus_kb.tools.centroid_tools as centroid_tools_module
import corpus_kb.tools.classification_tools as classification_tools_module
import corpus_kb.tools.cluster_tools as cluster_tools_module
import corpus_kb.tools.coding_tools as coding_tools_module
import corpus_kb.tools.conflicts_tools as conflicts_tools_module
import corpus_kb.tools.events_tools as events_tools_module
import corpus_kb.tools.extraction_tune_tools as extraction_tune_tools_module
import corpus_kb.tools.ingest_common as ingest_common_module
import corpus_kb.tools.ingest_reingest as ingest_reingest_module
import corpus_kb.tools.ingest_surface_tools as ingest_surface_tools_module
import corpus_kb.tools.keyphrase_tools as keyphrase_tools_module
import corpus_kb.tools.labeling_gold_tools as labeling_gold_tools_module
import corpus_kb.tools.labeling_tools as labeling_tools_module
import corpus_kb.tools.labeling_triage_tools as labeling_triage_tools_module
import corpus_kb.tools.lexical_tools as lexical_tools_module
import corpus_kb.tools.meta_embed_tools as meta_embed_tools_module
import corpus_kb.tools.method_select_tools as method_select_tools_module
import corpus_kb.tools.neighbor_tools as neighbor_tools_module
import corpus_kb.tools.nli_tools as nli_tools_module
import corpus_kb.tools.notebook_tools as notebook_tools_module
import corpus_kb.tools.project_tools as project_tools_module
import corpus_kb.tools.queue_tools as queue_tools_module
import corpus_kb.tools.release_tools as release_tools_module
import corpus_kb.tools.research_tools as research_tools_module
import corpus_kb.tools.search_tools as search_tools_module
import corpus_kb.tools.sentiment_tools as sentiment_tools_module
import corpus_kb.tools.statements_tools as statements_tools_module
import corpus_kb.tools.tag_tools as tag_tools_module
import corpus_kb.tools.topics_tools as topics_tools_module
import corpus_kb.tools.uxr_method_tools as uxr_method_tools_module
import corpus_kb.tools.uxr_survey_codes as uxr_survey_codes_module
import corpus_kb.tools.vector_screen_tools as vector_screen_tools_module
from corpus_kb.domain.models import RouteDecision, RoutedQuery, RoutedResult, VerifyAnswerResult
from corpus_kb.tools.ingest_tools import list_documents as list_documents_tool

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

# Canned envelopes for the promoted research ops: the key sets the
# shared tool layer really returns.
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
# The schema-pinned artifact: the real SCHEMA_VERSION or a missing report.
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

# Canned coding envelopes (issue #350): patched onto the CodingHandler
# methods so all four surfaces answer one envelope.
_EMBED_CODEBOOK = {"status": "success", "codes_embedded": 2, "codebook_version_id": CV}
_POOL = {
    "status": "success",
    "keyword_hits_written": 0,
    "chunk_signals_written": 0,
    "coverage": {},
    "keyword_governance": {},
    "speaker_roles": {},
}
_CODE_BATCH = {
    "status": "success",
    "cells_coded": 1,
    "cells_rejected": 0,
    "cells_routed": 1,
    "results": [],
}
_RELIABILITY = {
    "status": "success",
    "coders": [],
    "n_sheets": 0,
    "sampled_cells": 0,
    "sampled_chunks": 0,
    "insufficient_codes": [],
    "insufficient_sample": True,
    "overall": {},
    "per_code": [],
    "flags": {},
}
_CALIBRATE = {
    "status": "success",
    "code_id": "code-1",
    "pool_floor": 0.5,
    "residual_floor": 0.2,
    "n_positive_used": 1,
    "n_negative_used": 1,
    "n_positive_requested": 1,
    "n_negative_requested": 1,
    "fallback_applied": False,
}
_SATURATION = {
    "status": "success",
    "unique_codes": 1,
    "total_applications": 3,
    "isr": 0.25,
    "batch_id": None,
    "new_codes": None,
    "base_unique": None,
    "should_stop": None,
}


# -- Shared-leg implementations (the ONE function each surface routes via) --


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


class _FakeUploadSource:
    """One uploaded file in memory (the tools' UploadSource protocol shape)."""

    def __init__(self, name: str, content_type: str, data: bytes) -> None:
        self.filename = name
        self.content_type = content_type
        self.size = len(data)
        self._data = data

    async def read(self, size: int = -1) -> bytes:
        chunk = self._data if size < 0 else self._data[:size]
        self._data = b"" if size < 0 else self._data[len(chunk) :]
        return chunk


async def _shared_ingest_upload(ctx: dict[str, Any]) -> dict[str, Any]:
    from corpus_kb.tools.ingest_upload_tools import ingest_upload

    # The upload path carries no config block: the ambient config is the
    # real contract, so the shared leg makes the same call.
    source = _FakeUploadSource("parity.txt", "text/plain", TEXT_BODY.encode())
    return await ingest_upload(source, ctx["pool"])


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


async def _shared_ingest_reingest(ctx: dict[str, Any]) -> dict[str, Any]:
    return await ingest_reingest_module.ingest_reingest(ctx["pool"], config=ctx["config"])


async def _shared_demo_load(ctx: dict[str, Any]) -> dict[str, Any]:
    from corpus_kb.demo_kb import load_demo_kb

    # The demo path carries no config block: the ambient config is the
    # real contract, so the shared leg makes the same call.
    return await load_demo_kb(None, ctx["pool"])


async def _shared_topics_build(ctx: dict[str, Any]) -> dict[str, Any]:
    return await topics_tools_module.topics_build(ctx["pool"], config=ctx["config"])


async def _shared_topics_list(ctx: dict[str, Any]) -> dict[str, Any]:
    return await topics_tools_module.topics_list(ctx["pool"], config=ctx["config"], limit=50)


# -- Analysis program Wave 0 (stub handlers; disabled envelope is the
#    shared contract, exactly like the topics ops) --


async def _shared_topics_trends(ctx: dict[str, Any]) -> dict[str, Any]:
    return await topics_tools_module.topics_trends(ctx["pool"], config=ctx["config"])


async def _shared_labeling_build(ctx: dict[str, Any]) -> dict[str, Any]:
    return await labeling_tools_module.labeling_build(ctx["pool"], config=ctx["config"])


async def _shared_labeling_list(ctx: dict[str, Any]) -> dict[str, Any]:
    return await labeling_tools_module.labeling_list(ctx["pool"], config=ctx["config"], limit=50)


async def _shared_labeling_lfs(ctx: dict[str, Any]) -> dict[str, Any]:
    return await labeling_tools_module.labeling_lfs(ctx["pool"], config=ctx["config"], limit=50)


async def _shared_labeling_triage(ctx: dict[str, Any]) -> dict[str, Any]:
    # cv_folds=2: the canned world holds 4 labeled units in 2 classes, so
    # the leg exercises the REAL triage run, not just the guard envelope.
    return await labeling_triage_tools_module.labeling_triage(
        ctx["pool"], config=ctx["config"], cv_folds=2
    )


async def _shared_labeling_gold_check(ctx: dict[str, Any]) -> dict[str, Any]:
    return await labeling_gold_tools_module.labeling_gold_check(
        ctx["pool"], config=ctx["config"], gold_file=str(ctx[_GOLD_PATH])
    )


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


# -- AMR program wave 2 (real handlers; stubbed world answers on each
#    op's honest guard path or the module's own test seams) --


async def _shared_analysis_select_method(ctx: dict[str, Any]) -> dict[str, Any]:
    return await method_select_tools_module.analysis_select_method(
        ctx["pool"],
        config=ctx["config"],
        task_description="Rank methods for labeling interview themes.",
        output_family="labels",
        inputs_available=["embeddings", "labels"],
        max_complexity_tier="standard",
    )


# The route leg stacks four authored phrases of method 29 (phrase path
# scores 4/6, embedding path resolves on the cosine); both modes answer
# the same guaranteed key set, so the leg is stable everywhere.
_ROUTE_QUESTION = (
    "Find duplicate or paraphrased records before analysis. "
    "Which records say nearly the same thing? "
    "I want to merge near duplicates without losing speakers. "
    "Detect repeated submissions and rephrased copies."
)


async def _shared_analysis_route(ctx: dict[str, Any]) -> dict[str, Any]:
    return await analysis_route_tools_module.analysis_route(
        ctx["pool"],
        config=ctx["config"],
        question=_ROUTE_QUESTION,
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
    """Seed doc + chunk + two statements + one flag; return the flag id
    (idempotent on the deterministic hashes: re-runs reuse the rows)."""
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


# -- Textbook program W1: the queue ops. The stub pool answers the
#    canned _QUEUE_ROW for every ingest_queue statement.


async def _shared_queue_enqueue(ctx: dict[str, Any]) -> dict[str, Any]:
    return await queue_tools_module.queue_enqueue(
        ctx["pool"], config=ctx["config"], path=str(ctx[_FILE_PATH])
    )


async def _shared_queue_status(ctx: dict[str, Any]) -> dict[str, Any]:
    return await queue_tools_module.queue_status(ctx["pool"], config=ctx["config"])


async def _shared_queue_cancel(ctx: dict[str, Any]) -> dict[str, Any]:
    return await queue_tools_module.queue_cancel(
        ctx["pool"], config=ctx["config"], queue_id=_QUEUE_ID
    )


# -- Track C: the per-project raw-table ops (the stub pool answers
#    provision's already-provisioned shape, empty list, one drop row).


async def _shared_project_provision(ctx: dict[str, Any]) -> dict[str, Any]:
    return await project_tools_module.project_provision(ctx["pool"], slug="parity-notes")


async def _shared_project_list(ctx: dict[str, Any]) -> dict[str, Any]:
    return await project_tools_module.project_list(ctx["pool"])


async def _shared_project_drop(ctx: dict[str, Any]) -> dict[str, Any]:
    return await project_tools_module.project_drop(ctx["pool"], slug="parity-notes")


async def _shared_project_claim(ctx: dict[str, Any]) -> dict[str, Any]:
    return await project_tools_module.project_claim(ctx["pool"], doc_id=DOC_ID, slug=_PROJECT_SLUG)


async def _shared_project_claim_list(ctx: dict[str, Any]) -> dict[str, Any]:
    return await project_tools_module.project_claim_list(ctx["pool"])


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
    # dry_run: no filesystem contract in the parity world; the envelope
    # shape and the canned mine row are what the legs pin.
    return await extraction_tune_tools_module.extraction_tune(
        ctx["pool"], config=ctx["config"], dry_run=True
    )


async def _shared_analysis_embed_meta(ctx: dict[str, Any]) -> dict[str, Any]:
    # document kind only: the profile kind calls the real embedder.
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
    # k stays at or below the canned corpus minus two: the k-th-nearest
    # scan must never meet the self marker.
    return await vector_screen_tools_module.outliers_rank(ctx["pool"], config=ctx["config"], k=2)


async def _shared_nli_classify(ctx: dict[str, Any]) -> dict[str, Any]:
    return await nli_tools_module.nli_classify(
        ctx["pool"], config=ctx["config"], labels=["reports", "meetings"]
    )


async def _shared_contradictions_screen(ctx: dict[str, Any]) -> dict[str, Any]:
    return await nli_tools_module.contradictions_screen(ctx["pool"], config=ctx["config"])


async def _shared_sentiment_screen(ctx: dict[str, Any]) -> dict[str, Any]:
    return await sentiment_tools_module.sentiment_screen(ctx["pool"], config=ctx["config"])


async def _shared_survey_openend_codes(ctx: dict[str, Any]) -> dict[str, Any]:
    return await uxr_survey_codes_module.survey_openend_codes(ctx["pool"], config=ctx["config"])


async def _shared_usability_severity(ctx: dict[str, Any]) -> dict[str, Any]:
    return await uxr_method_tools_module.usability_severity(ctx["pool"], config=ctx["config"])


async def _shared_emotion_tag(ctx: dict[str, Any]) -> dict[str, Any]:
    return await uxr_method_tools_module.emotion_tag(ctx["pool"], config=ctx["config"])


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


async def _shared_search_similar(ctx: dict[str, Any]) -> dict[str, Any]:
    return await search_tools_module.search_similar(ctx["pool"], DOC_ID, config=ctx["config"], k=10)


async def _shared_search_context(ctx: dict[str, Any]) -> dict[str, Any]:
    return await search_tools_module.search_context(
        ctx["pool"], "parity probe", config=ctx["config"], k=5
    )


async def _shared_get_document_tags(ctx: dict[str, Any]) -> dict[str, Any]:
    return await tag_tools_module.get_document_tags(ctx["pool"], DOC_ID)


async def _shared_get_metadata(ctx: dict[str, Any]) -> dict[str, Any]:
    return await tag_tools_module.get_metadata(ctx["pool"], key="parity")


async def _shared_calibrate_apply(ctx: dict[str, Any]) -> dict[str, Any]:
    from corpus_kb.tools.calibrate_tools import calibrate_apply

    return await calibrate_apply("parity", write=True, config=ctx["config"])


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


# -- Promoted research ops: the shared tool layer is the ONE function --


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

    return await research_cycle(ctx["pool"], mode="in", ingest_dir=ctx.get("ingest_dir"))


async def _shared_research_demo(ctx: dict[str, Any]) -> dict[str, Any]:
    from corpus_kb.tools.demo_tools import research_demo

    return await research_demo()


# -- Issue #350: the chunk-coding ops + the event-store health read (the
#    canned CodingHandler methods answer every surface identically).


async def _shared_coding_embed_codebook(ctx: dict[str, Any]) -> dict[str, Any]:
    return await coding_tools_module.coding_embed_codebook(
        ctx["pool"], {"categories": []}, config=ctx["config"]
    )


async def _shared_coding_pool(ctx: dict[str, Any]) -> dict[str, Any]:
    return await coding_tools_module.coding_pool(ctx["pool"], CV, config=ctx["config"])


async def _shared_coding_code_batch(ctx: dict[str, Any]) -> dict[str, Any]:
    return await coding_tools_module.coding_code_batch(
        ctx["pool"], [{"chunk_id": DOC_ID, "code_ids": ["code-1"]}], config=ctx["config"]
    )


async def _shared_coding_reliability(ctx: dict[str, Any]) -> dict[str, Any]:
    return await coding_tools_module.coding_reliability(
        ctx["pool"], config=ctx["config"], per_code_min=5, seed=17
    )


async def _shared_coding_calibrate_floors(ctx: dict[str, Any]) -> dict[str, Any]:
    return await coding_tools_module.coding_calibrate_floors(
        ctx["pool"], "code-1", CV, config=ctx["config"]
    )


async def _shared_coding_saturation(ctx: dict[str, Any]) -> dict[str, Any]:
    return await coding_tools_module.coding_saturation(ctx["pool"], config=ctx["config"])


async def _shared_coding_status(ctx: dict[str, Any]) -> dict[str, Any]:
    return await coding_tools_module.coding_status(ctx["pool"], config=ctx["config"])


async def _shared_coding_export(ctx: dict[str, Any]) -> dict[str, Any]:
    return await coding_tools_module.coding_export(
        ctx["pool"], config=ctx["config"], limit=10, offset=0
    )


async def _shared_events_summary(ctx: dict[str, Any]) -> dict[str, Any]:
    return await events_tools_module.events_summary(ctx["pool"], config=ctx["config"])


def _post(path: str, body: dict[str, Any]) -> Any:
    def spec(ctx: dict[str, Any]) -> dict[str, Any]:
        return {"method": "post", "path": path, "json": dict(body)}

    return spec


def _get(path: str, params: dict[str, Any]) -> Any:
    def spec(ctx: dict[str, Any]) -> dict[str, Any]:
        return {"method": "get", "path": path, "params": dict(params)}

    return spec


def _post_multipart(path: str, filename: str, content: str) -> Any:
    """One multipart-form POST leg (the upload route's shape)."""

    def spec(ctx: dict[str, Any]) -> dict[str, Any]:
        return {
            "method": "post_multipart",
            "path": path,
            "files": {"file": (filename, content, "text/plain")},
        }

    return spec


@dataclass(frozen=True)
class _Leg:
    """The invocation plan for one catalogue operation."""

    shared: Callable[[dict[str, Any]], Awaitable[dict[str, Any]]]
    http: Callable[[dict[str, Any]], dict[str, Any]]
    socket: Callable[[dict[str, Any]], tuple[str, dict[str, Any]]] | None = None
    # MCP tool arguments (ops with an MCP surface; the catalogue
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
_GOLD_PATH = "gold_path"
DOC_ID = "00000000-0000-0000-0000-00000000000d"
_QUEUE_ID = "00000000-0000-0000-0000-0000000000e1"

_PROJECT_SLUG = "parity-notes"

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
    # Multipart upload (ruling 4a): HTTP only, so the leg pair is
    # shared + http alone; the missing legs are DECLARED-EXEMPT, not skips.
    "ingest_upload": _Leg(
        _shared_ingest_upload,
        _post_multipart("/api/ingest/upload", "parity.txt", TEXT_BODY),
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
    "ingest_reingest": _Leg(
        _shared_ingest_reingest,
        _post("/api/ingest/reingest", {}),
        _sock("ingest_reingest", {}),
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
    # Label-quality pair (U39 triage + F6 gold check, canned world).
    "labeling_triage": _Leg(
        _shared_labeling_triage,
        _post("/api/labeling/triage", {"cv_folds": 2}),
        _sock("labeling_triage", {"cv_folds": 2}),
        _mcp({"cv_folds": 2}),
    ),
    "labeling_gold_check": _Leg(
        _shared_labeling_gold_check,
        lambda ctx: {
            "method": "post",
            "path": "/api/labeling/gold-check",
            "json": {"gold_file": str(ctx[_GOLD_PATH])},
        },
        lambda ctx: ("labeling_gold_check", {"gold_file": str(ctx[_GOLD_PATH])}),
        lambda ctx: {"gold_file": str(ctx[_GOLD_PATH])},
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
    # -- analysis question-routing program (wave 3): the question front door -
    "analysis_route": _Leg(
        _shared_analysis_route,
        _post("/api/analysis/route", {"question": _ROUTE_QUESTION}),
        _sock("analysis_route", {"question": _ROUTE_QUESTION}),
        _mcp({"question": _ROUTE_QUESTION}),
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
    # -- Living Corpus W4: the belief ops (timeline/predict/compare legs
    #    seed one like->dislike arc and rebuild before reading).
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
    # -- Textbook program W1: the queue ops. The enqueue leg points at
    #    the tmp file; the stub pool answers the canned pending row.
    "queue_enqueue": _Leg(
        _shared_queue_enqueue,
        lambda ctx: {
            "method": "post",
            "path": "/api/queue/enqueue",
            "json": {"path": str(ctx[_FILE_PATH])},
        },
        lambda ctx: ("queue_enqueue", {"path": str(ctx[_FILE_PATH])}),
        lambda ctx: {"path": str(ctx[_FILE_PATH])},
    ),
    "queue_status": _Leg(
        _shared_queue_status,
        _post("/api/queue/status", {}),
        _sock("queue_status", {}),
        _mcp({}),
    ),
    "queue_cancel": _Leg(
        _shared_queue_cancel,
        _post("/api/queue/cancel", {"queue_id": _QUEUE_ID}),
        _sock("queue_cancel", {"queue_id": _QUEUE_ID}),
        _mcp({"queue_id": _QUEUE_ID}),
    ),
    # -- Track C: the per-project raw-table ops. --
    "project_provision": _Leg(
        _shared_project_provision,
        _post("/api/project/provision", {"slug": _PROJECT_SLUG}),
        _sock("project_provision", {"slug": _PROJECT_SLUG}),
        _mcp({"slug": _PROJECT_SLUG}),
    ),
    "project_list": _Leg(
        _shared_project_list,
        _post("/api/project/list", {}),
        _sock("project_list", {}),
        _mcp({}),
    ),
    "project_drop": _Leg(
        _shared_project_drop,
        _post("/api/project/drop", {"slug": _PROJECT_SLUG}),
        _sock("project_drop", {"slug": _PROJECT_SLUG}),
        _mcp({"slug": _PROJECT_SLUG}),
    ),
    "project_claim": _Leg(
        _shared_project_claim,
        _post("/api/project/claim", {"doc_id": DOC_ID, "slug": _PROJECT_SLUG}),
        _sock("project_claim", {"doc_id": DOC_ID, "slug": _PROJECT_SLUG}),
        _mcp({"doc_id": DOC_ID, "slug": _PROJECT_SLUG}),
    ),
    "project_claim_list": _Leg(
        _shared_project_claim_list,
        _post("/api/project/claim-list", {"doc_id": DOC_ID, "slug": _PROJECT_SLUG}),
        _sock("project_claim_list", {"doc_id": DOC_ID, "slug": _PROJECT_SLUG}),
        _mcp({"doc_id": DOC_ID, "slug": _PROJECT_SLUG}),
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
    "survey_openend_codes": _Leg(
        _shared_survey_openend_codes,
        _post("/api/analysis/survey-openend-codes", {}),
        _sock("survey_openend_codes", {}),
        _mcp({}),
    ),
    "usability_severity": _Leg(
        _shared_usability_severity,
        _post("/api/analysis/usability-severity", {}),
        _sock("usability_severity", {}),
        _mcp({}),
    ),
    "emotion_tag": _Leg(
        _shared_emotion_tag,
        _post("/api/analysis/emotion-tag", {}),
        _sock("emotion_tag", {}),
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
    # -- issue #182 read ops: documents tags/metadata + the search reads ----
    "get_document_tags": _Leg(
        _shared_get_document_tags,
        _get(f"/api/documents/{DOC_ID}/tags", {}),
        _sock("get_document_tags", {"doc_id": DOC_ID}),
        _mcp({"doc_id": DOC_ID}),
    ),
    "get_metadata": _Leg(
        _shared_get_metadata,
        _get("/api/metadata", {"key": "parity"}),
        _sock("get_metadata", {"key": "parity"}),
        _mcp({}),
    ),
    "search_similar": _Leg(
        _shared_search_similar,
        _post("/api/search/similar", {"chunk_id": DOC_ID, "k": 10}),
        _sock("search_similar", {"chunk_id": DOC_ID}),
        _mcp({"chunk_id": DOC_ID, "k": 10}),
    ),
    "search_context": _Leg(
        _shared_search_context,
        _post("/api/search/context", {"query": "parity probe", "k": 5}),
        _sock("search_context", {"query": "parity probe"}),
        _mcp({"query": "parity probe", "k": 5}),
    ),
    # -- issue #200: the calibrate apply op writes the tmp config file ------
    "calibrate_apply": _Leg(
        _shared_calibrate_apply,
        _post("/api/calibrate/apply", {"profile": "parity", "write": True}),
        _sock("calibrate_apply", {"profile": "parity", "write": True}),
        _mcp({"profile": "parity", "write": True}),
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
    # Lifecycle UI op (momus F8 close-out): cli+mcp+http only; the
    # absent socket surface is the row's declaration, not a skip.
    "demo_load": _Leg(
        _shared_demo_load,
        _post("/api/demo/load", {}),
        mcp=_mcp({}),
    ),
    # -- Issue #350: the chunk-coding ops + the event-store health read ----
    "coding_embed_codebook": _Leg(
        _shared_coding_embed_codebook,
        _post("/api/coding/embed-codebook", {"codebook": {"categories": []}}),
        _sock("coding_embed_codebook", {"codebook": {"categories": []}}),
        _mcp({"codebook": {"categories": []}}),
    ),
    "coding_pool": _Leg(
        _shared_coding_pool,
        _post("/api/coding/pool", {"codebook_version_id": CV}),
        _sock("coding_pool", {"codebook_version_id": CV}),
        _mcp({"codebook_version_id": CV}),
    ),
    "coding_code_batch": _Leg(
        _shared_coding_code_batch,
        _post("/api/coding/code-batch", {"cells": [{"chunk_id": DOC_ID, "code_ids": ["code-1"]}]}),
        _sock("coding_code_batch", {"cells": [{"chunk_id": DOC_ID, "code_ids": ["code-1"]}]}),
        _mcp({"cells": [{"chunk_id": DOC_ID, "code_ids": ["code-1"]}]}),
    ),
    "coding_reliability": _Leg(
        _shared_coding_reliability,
        _post("/api/coding/reliability", {"per_code_min": 5, "seed": 17}),
        _sock("coding_reliability", {"per_code_min": 5, "seed": 17}),
        _mcp({"per_code_min": 5, "seed": 17}),
    ),
    "coding_calibrate_floors": _Leg(
        _shared_coding_calibrate_floors,
        _post("/api/coding/calibrate-floors", {"code_id": "code-1", "codebook_version_id": CV}),
        _sock("coding_calibrate_floors", {"code_id": "code-1", "codebook_version_id": CV}),
        _mcp({"code_id": "code-1", "codebook_version_id": CV}),
    ),
    "coding_saturation": _Leg(
        _shared_coding_saturation,
        _post("/api/coding/saturation", {}),
        _sock("coding_saturation", {}),
        _mcp({}),
    ),
    "coding_status": _Leg(
        _shared_coding_status,
        _get("/api/coding/status", {}),
        _sock("coding_status", {}),
        _mcp({}),
    ),
    "coding_export": _Leg(
        _shared_coding_export,
        _post("/api/coding/export", {"limit": 10, "offset": 0}),
        _sock("coding_export", {"limit": 10, "offset": 0}),
        _mcp({"limit": 10, "offset": 0}),
    ),
    "events_summary": _Leg(
        _shared_events_summary,
        _get("/api/events/summary", {}),
        _sock("events_summary", {}),
        _mcp({}),
    ),
}


async def _shared_release_status(ctx: dict[str, Any]) -> dict[str, Any]:
    return await release_tools_module.release_status(ctx["pool"], CV)


# -- Offline stubs (pool / pipeline store / eventsourcing app / handlers) --

#: The canned ingest_queue row every queue statement returns on the stub
#: pool: the SELECT column set of queue/store.py, one pending row.
_QUEUE_ROW: dict[str, Any] = {
    "id": _QUEUE_ID,
    "tenant_id": TENANT,
    "path": "/tmp/parity-book.pdf",
    "title": "parity textbook",
    "project_id": None,
    "profile": None,
    "strategy_config": "{}",
    "status": "pending",
    "attempts": 0,
    "max_attempts": 3,
    "last_error": None,
    "run_id": None,
    "content_hash": "parityhash",
    "claimed_by": None,
    "heartbeat_at": None,
    "queued_at": datetime(2026, 10, 28, 12, 0, 0, tzinfo=UTC),
    "started_at": None,
    "finished_at": None,
    "updated_at": None,
}
