"""The operation catalogue: release + research families, and the SKIP set.

Same contract as ``catalogue.py`` (read its docstring first). This module
assembles the FINAL catalogue: ``OPERATIONS`` is the tuple every consumer
(the parity gate, ``corpus-kb operations``) iterates.

``SKIPPED_OPERATIONS`` lists operations with two or more surfaces that the
parity gate does NOT run, each with an honest reason. They still appear in
the inventory, marked ``parity: "skipped"``.
"""

from __future__ import annotations

from corpus_kb.contracts.catalogue import CORE_OPERATIONS
from corpus_kb.contracts.model import (
    CLI,
    DEFAULT_TENANT_ID,
    HTTP,
    MCP,
    Operation,
    ParamSpec,
    SkippedOperation,
    SurfaceSpec,
)

_TENANT_PARAM = ParamSpec("tenant_id", default=DEFAULT_TENANT_ID)

# Release lifecycle (U5, v6 section 6): one handler per op in
# handlers/release_handlers.py; the tool layer (tools/release_tools.py)
# serializes each result with to_payload(). The canned parity stubs return
# exactly these key sets.
_RESEARCH_OPERATIONS: tuple[Operation, ...] = (
    Operation(
        name="release_assess",
        family="release",
        description="Compose every release gate over current evidence; writes nothing.",
        handler="corpus_kb.handlers.release_handlers:assess_release",
        params=(
            ParamSpec("codebook_version_id", required=True),
            ParamSpec("profile", default=None),
            ParamSpec("run_id", default=None),
            _TENANT_PARAM,
        ),
        envelope_keys=(
            "status",
            "codebook_version_id",
            "profile",
            "enforcement",
            "gate_results",
            "required_gate_ids",
            "required_failing",
            "required_not_evaluable",
            "all_required_pass_without_waivers",
            "data_sufficiency",
        ),
        surfaces=(
            SurfaceSpec(MCP, "release_assess"),
            SurfaceSpec(CLI, "release assess"),
            SurfaceSpec(HTTP, "/api/release/assess", "POST"),
        ),
    ),
    Operation(
        name="release_request",
        family="release",
        description="Open a draft release pinned to the current content hash.",
        handler="corpus_kb.handlers.release_handlers:request_release",
        params=(
            ParamSpec("codebook_version_id", required=True),
            ParamSpec("creator", required=True),
            ParamSpec("codebook_id", default=None),
            ParamSpec("profile", default=None),
            ParamSpec("release_id", default=None),
            _TENANT_PARAM,
        ),
        envelope_keys=("status", "release_id", "manifest_sha256", "profile"),
        surfaces=(
            SurfaceSpec(MCP, "release_request"),
            SurfaceSpec(CLI, "release request"),
            SurfaceSpec(HTTP, "/api/release/request", "POST"),
        ),
    ),
    Operation(
        name="release_review",
        family="release",
        description="Show the review packet: gates, waivers, and next actions.",
        handler="corpus_kb.handlers.release_handlers:review_release",
        params=(
            ParamSpec("codebook_version_id", required=True),
            ParamSpec("release_id", required=True),
            _TENANT_PARAM,
        ),
        envelope_keys=(
            "status",
            "release_id",
            "release_state",
            "profile",
            "enforcement",
            "evidence",
            "failing_gates",
            "not_evaluable_gates",
            "waived_gates",
            "unmet_required_gates",
            "next_actions",
        ),
        surfaces=(
            SurfaceSpec(MCP, "release_review"),
            SurfaceSpec(CLI, "release review"),
            SurfaceSpec(HTTP, "/api/release/review", "POST"),
        ),
    ),
    Operation(
        name="release_execute",
        family="release",
        description="Execute the release with a named human approver.",
        handler="corpus_kb.handlers.release_handlers:execute_release",
        params=(
            ParamSpec("codebook_version_id", required=True),
            ParamSpec("release_id", required=True),
            ParamSpec("approver", required=True),
            ParamSpec("waivers", default=None),
            ParamSpec("run_id", default=None),
            _TENANT_PARAM,
        ),
        envelope_keys=(
            "status",
            "release_id",
            "approver",
            "enforcement",
            "unmet_required_gates",
            "warnings",
            "reason",
        ),
        surfaces=(
            SurfaceSpec(MCP, "release_execute"),
            SurfaceSpec(CLI, "release release"),
            SurfaceSpec(HTTP, "/api/release/release", "POST"),
        ),
        exit_codes=(0, 1),
    ),
    Operation(
        name="release_status",
        family="release",
        description="Show stage, release state, blockers, and the next command.",
        handler="corpus_kb.handlers.release_handlers:status_report",
        params=(
            ParamSpec("codebook_version_id", required=True),
            _TENANT_PARAM,
        ),
        envelope_keys=(
            "status",
            "codebook_version_id",
            "current_stage",
            "release_state",
            "blocking_criteria",
            "recommended_next_action",
            "active_release",
            "draft",
            "pending_change_proposals",
            "data_sufficiency",
        ),
        surfaces=(
            SurfaceSpec(MCP, "release_status"),
            SurfaceSpec(CLI, "release status"),
            SurfaceSpec(HTTP, "/api/release/status", "GET"),
        ),
    ),
    Operation(
        name="release_compare",
        family="release",
        description="Diff the manifests of two releases of one codebook version.",
        handler="corpus_kb.handlers.release_handlers:compare_version_releases",
        params=(
            ParamSpec("codebook_version_id", required=True),
            ParamSpec("old_release_id", required=True),
            ParamSpec("new_release_id", required=True),
            _TENANT_PARAM,
        ),
        # The diff keys are dynamic (merged from the manifest diff); the
        # three keys below are the guaranteed core.
        envelope_keys=("status", "old_release_id", "new_release_id"),
        surfaces=(
            SurfaceSpec(MCP, "release_compare"),
            SurfaceSpec(CLI, "release compare"),
            SurfaceSpec(HTTP, "/api/release/compare", "POST"),
        ),
    ),
    Operation(
        name="research_ingest_transcript",
        family="research",
        description="Ingest one transcript file: turns, roles, and exchanges.",
        handler="corpus_kb.handlers.research_handler:ResearchHandler.ingest_transcript",
        params=(
            ParamSpec("file_path", required=True),
            ParamSpec("project_id", default=None),
            ParamSpec("title", default=None),
            _TENANT_PARAM,
            ParamSpec("force", default=False),
        ),
        envelope_keys=(
            "status",
            "doc_id",
            "path",
            "n_turns",
            "n_exchanges",
            "source_type",
            "parse_quality",
            "file_sha256",
        ),
        surfaces=(
            SurfaceSpec(MCP, "research_ingest_transcript"),
            SurfaceSpec(CLI, "research ingest-transcript"),
            SurfaceSpec(HTTP, "/api/research/ingest-transcript", "POST"),
        ),
    ),
    Operation(
        name="notebook_ask",
        family="research",
        description="Grounded question answering with cited sentences.",
        handler="corpus_kb.research.notebook:notebook_ask",
        params=(
            ParamSpec("question", required=True),
            ParamSpec("project_id", required=True),
            ParamSpec("k", default=6),
            ParamSpec("retrieval_only", default=False),
            ParamSpec("source_type", default=None),
            ParamSpec("speaker_role", default=None),
            ParamSpec("topic_id", default=None),
            ParamSpec("doc_ids", default=None),
            ParamSpec("code", default=None),
            ParamSpec("min_confidence", default=None),
            ParamSpec("review_status", default=None),
            _TENANT_PARAM,
        ),
        # The payload shape depends on the outcome branch (answer, evidence,
        # empty); status + llm_calls are the guaranteed core.
        envelope_keys=("status", "llm_calls"),
        surfaces=(
            SurfaceSpec(MCP, "notebook_ask"),
            SurfaceSpec(CLI, "research ask"),
            SurfaceSpec(HTTP, "/api/notebook/ask", "POST"),
        ),
    ),
)

OPERATIONS: tuple[Operation, ...] = (*CORE_OPERATIONS, *_RESEARCH_OPERATIONS)

SKIPPED_OPERATIONS: tuple[SkippedOperation, ...] = (
    SkippedOperation(
        name="ingest_directory",
        family="ingest",
        reason=(
            "Two directory pipelines exist (tools.ingest_tools.ingest_directory "
            "vs handlers.command_handler.handle_ingest_directory) and their "
            "envelopes differ. Known drift; the fix belongs to the ingest "
            "parity package, not a silent catalogue row."
        ),
        surfaces=(
            SurfaceSpec(MCP, "ingest_directory"),
            SurfaceSpec(CLI, "ingest <dir>"),
            SurfaceSpec(HTTP, "/api/ingest/directory", "POST"),
        ),
    ),
    SkippedOperation(
        name="delete_document",
        family="documents",
        reason=(
            "The tool deletes through direct SQL; HTTP deletes through the "
            "event-store aggregate. Two pipelines, one operation. A hermetic "
            "HTTP leg also needs an event-store repository stub."
        ),
        surfaces=(
            SurfaceSpec(MCP, "delete_document"),
            SurfaceSpec(HTTP, "/api/documents/{doc_id}", "DELETE"),
        ),
    ),
    SkippedOperation(
        name="research_ingest",
        family="research",
        reason=(
            "The handler dedups each path against the live ingest ledger in "
            "Postgres. Stubbing it would only re-prove the route wiring "
            "already pinned by research_ingest_transcript."
        ),
        surfaces=(
            SurfaceSpec(MCP, "research_ingest"),
            SurfaceSpec(CLI, "research ingest"),
            SurfaceSpec(HTTP, "/api/research/ingest", "POST"),
        ),
    ),
    SkippedOperation(
        name="coding_run",
        family="research",
        reason="Needs a live embedder and database for three-view scoring.",
        surfaces=(
            SurfaceSpec(MCP, "coding_run"),
            SurfaceSpec(CLI, "coding run"),
            SurfaceSpec(HTTP, "/api/coding/run", "POST"),
        ),
    ),
    SkippedOperation(
        name="inductive_run",
        family="research",
        reason="Needs a live LLM plus the optional UMAP/HDBSCAN extras.",
        surfaces=(
            SurfaceSpec(MCP, "inductive_run"),
            SurfaceSpec(CLI, "coding inductive"),
            SurfaceSpec(HTTP, "/api/coding/inductive", "POST"),
        ),
    ),
    SkippedOperation(
        name="codebook_promote",
        family="research",
        reason="Writes codebook events and projections; needs a live database.",
        surfaces=(
            SurfaceSpec(MCP, "codebook_promote"),
            SurfaceSpec(CLI, "codebook promote"),
            SurfaceSpec(HTTP, "/api/codebook/promote", "POST"),
        ),
    ),
    SkippedOperation(
        name="research_report",
        family="research",
        reason="Builds the governance report from database aggregates.",
        surfaces=(
            SurfaceSpec(MCP, "research_report"),
            SurfaceSpec(CLI, "research report"),
            SurfaceSpec(HTTP, "/api/research/report", "POST"),
        ),
    ),
    SkippedOperation(
        name="review_execute",
        family="research",
        reason="Writes review events through the event store and projections.",
        surfaces=(
            SurfaceSpec(MCP, "review_execute"),
            SurfaceSpec(CLI, "review accept"),
            SurfaceSpec(HTTP, "/api/review/execute", "POST"),
        ),
    ),
    SkippedOperation(
        name="notebook_evidence",
        family="research",
        reason="Ranks coded units from database evidence views.",
        surfaces=(
            SurfaceSpec(MCP, "notebook_evidence"),
            SurfaceSpec(CLI, "research evidence"),
            SurfaceSpec(HTTP, "/api/notebook/evidence", "POST"),
        ),
    ),
    SkippedOperation(
        name="notebook_uncoded",
        family="research",
        reason="Needs a live embedder to rank units by code margin.",
        surfaces=(
            SurfaceSpec(MCP, "notebook_uncoded"),
            SurfaceSpec(CLI, "research uncoded"),
            SurfaceSpec(HTTP, "/api/notebook/uncoded", "POST"),
        ),
    ),
    SkippedOperation(
        name="notebook_overlap",
        family="research",
        reason="Needs database views for the code-overlap metrics.",
        surfaces=(
            SurfaceSpec(MCP, "notebook_overlap"),
            SurfaceSpec(CLI, "research overlap"),
            SurfaceSpec(HTTP, "/api/notebook/overlap", "POST"),
        ),
    ),
    SkippedOperation(
        name="research_cycle",
        family="research",
        reason="Single surface (CLI) by design; interactive halt gates.",
        surfaces=(SurfaceSpec(CLI, "research cycle"),),
    ),
    SkippedOperation(
        name="research_demo",
        family="research",
        reason="Single surface (CLI) by design; narrated demo pipeline.",
        surfaces=(SurfaceSpec(CLI, "research demo"),),
    ),
)


def get_operation(name: str) -> Operation | None:
    """Return the catalogue row for one operation name, or None."""
    for op in OPERATIONS:
        if op.name == name:
            return op
    return None
