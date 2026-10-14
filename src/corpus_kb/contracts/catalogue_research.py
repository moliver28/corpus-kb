"""The operation catalogue: research-family rows and the SKIP policy.

``SKIPPED_OPERATIONS`` lists operations with two or more surfaces that the
parity gate does NOT run, each with an honest reason (live LLM/DB need,
single-surface design, or a known pipeline split tracked elsewhere). They
still appear in the inventory, marked ``parity: "skipped"``.
"""

from __future__ import annotations

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

_TENANT = ParamSpec("tenant_id", default=DEFAULT_TENANT_ID)

RESEARCH_OPERATIONS: tuple[Operation, ...] = (
    Operation(
        name="research_ingest_transcript",
        family="research",
        description="Ingest one transcript file: turns, roles, and exchanges.",
        handler="corpus_kb.handlers.research_handler:ResearchHandler.ingest_transcript",
        params=(
            ParamSpec("file_path", required=True),
            ParamSpec("project_id"),
            ParamSpec("title"),
            _TENANT,
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
            ParamSpec("source_type"),
            ParamSpec("speaker_role"),
            ParamSpec("topic_id"),
            ParamSpec("doc_ids"),
            ParamSpec("code"),
            ParamSpec("min_confidence"),
            ParamSpec("review_status"),
            _TENANT,
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
