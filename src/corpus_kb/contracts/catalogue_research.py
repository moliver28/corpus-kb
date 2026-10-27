"""The operation catalogue: research-family rows (full surface parity).

Every research/coding operation lives here as a full row: MCP, CLI, HTTP,
and socket surfaces, the shared tool layer that serves them all, and the
guaranteed-on-success envelope keys. The old SKIP set is gone: the parity
gate runs every row (the offline stubs live in the gate's harness), and
the inventory prints zero skipped operations.

Guaranteed keys only (the envelopes.py rule): outcome-specific keys (the
cycle's gate payload, the report's dynamic sections, promote's block
reason) stay off the tuples when they are conditional.

Long-running honesty: ``research_cycle`` and ``research_demo`` are
SYNCHRONOUS long-running calls on http/mcp/socket, exactly like the CLI.
``codebook_promote`` stays an EXPLICIT human act; no surface automates it.
"""

from __future__ import annotations

from corpus_kb.contracts.envelopes import VALIDATION_KEYS
from corpus_kb.contracts.model import (
    CLI,
    DEFAULT_TENANT_ID,
    HTTP,
    MCP,
    SOCKET,
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
        handler="corpus_kb.tools.research_tools:research_ingest_transcript",
        params=(
            ParamSpec("file_path", required=True),
            ParamSpec("project_id"),
            ParamSpec("title"),
            ParamSpec("validate"),
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
            SurfaceSpec(SOCKET, "research_ingest_transcript"),
        ),
        http_extra=VALIDATION_KEYS,
        socket_extra=VALIDATION_KEYS,
    ),
    Operation(
        name="research_ingest",
        family="research",
        description="Dynamic ingest of file/dir/glob with dedup; --watch tails a drop dir.",
        handler="corpus_kb.tools.research_tools:research_ingest",
        params=(
            ParamSpec("path", required=True),
            ParamSpec("project_id"),
            ParamSpec("validate"),
            _TENANT,
            ParamSpec("force", default=False),
        ),
        envelope_keys=("status", "files_found", "ingested", "skipped", "errors", "results"),
        surfaces=(
            SurfaceSpec(MCP, "research_ingest"),
            SurfaceSpec(CLI, "research ingest"),
            SurfaceSpec(HTTP, "/api/research/ingest", "POST"),
            SurfaceSpec(SOCKET, "research_ingest"),
        ),
        http_extra=VALIDATION_KEYS,
        socket_extra=VALIDATION_KEYS,
    ),
    Operation(
        name="coding_run",
        family="research",
        description="Deductive run (v5 section 8): three-view scoring, calibrated tau.",
        handler="corpus_kb.tools.research_tools:coding_run",
        params=(
            ParamSpec("codebook_version_id", required=True),
            ParamSpec("project_id"),
            _TENANT,
            ParamSpec("cal_alpha", default=0.1),
        ),
        envelope_keys=(
            "status",
            "run_id",
            "codebook_version_id",
            "n_units",
            "n_codes",
            "explicit",
            "question_dependent",
            "review",
        ),
        surfaces=(
            SurfaceSpec(MCP, "coding_run"),
            SurfaceSpec(CLI, "coding run"),
            SurfaceSpec(HTTP, "/api/coding/run", "POST"),
            SurfaceSpec(SOCKET, "coding_run"),
        ),
    ),
    Operation(
        name="inductive_run",
        family="research",
        description="Inductive pass (v5 section 9): summaries, clusters, proposed codes.",
        handler="corpus_kb.tools.research_tools:inductive_run",
        params=(ParamSpec("project_id"), _TENANT),
        envelope_keys=("status", "run_id", "n_units"),
        surfaces=(
            SurfaceSpec(MCP, "inductive_run"),
            SurfaceSpec(CLI, "coding inductive"),
            SurfaceSpec(HTTP, "/api/coding/inductive", "POST"),
            SurfaceSpec(SOCKET, "inductive_run"),
        ),
    ),
    Operation(
        name="codebook_promote",
        family="research",
        description=(
            "Promote one proposed code into a new codebook version. An "
            "explicit human act; no surface automates it."
        ),
        handler="corpus_kb.tools.research_tools:codebook_promote",
        params=(
            ParamSpec("proposed_id", required=True),
            ParamSpec("name", required=True),
            ParamSpec("definition", required=True),
            ParamSpec("inclusion", default=""),
            ParamSpec("exclusion", default=""),
            ParamSpec("label"),
            ParamSpec("tau_dup"),
            _TENANT,
        ),
        envelope_keys=("status", "proposal_id", "tau_dup"),
        surfaces=(
            SurfaceSpec(MCP, "codebook_promote"),
            SurfaceSpec(CLI, "codebook promote"),
            SurfaceSpec(HTTP, "/api/codebook/promote", "POST"),
            SurfaceSpec(SOCKET, "codebook_promote"),
        ),
        exit_codes=(0, 1, 3),
    ),
    Operation(
        name="research_report",
        family="research",
        description="Governance report (v5 sections 11/13/14): one schema-pinned artifact.",
        handler="corpus_kb.tools.research_tools:research_report",
        params=(
            ParamSpec("codebook_version_id"),
            ParamSpec("project_id"),
            _TENANT,
            ParamSpec("level", default="novice"),
        ),
        envelope_keys=(
            "schema_version",
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
        ),
        surfaces=(
            SurfaceSpec(MCP, "research_report"),
            SurfaceSpec(CLI, "research report"),
            SurfaceSpec(HTTP, "/api/research/report", "POST"),
            SurfaceSpec(SOCKET, "research_report"),
        ),
    ),
    Operation(
        name="review_execute",
        family="research",
        description="Record one human accept/override decision for one assignment.",
        handler="corpus_kb.tools.research_tools:review_execute",
        params=(
            ParamSpec("assignment_id", required=True),
            ParamSpec("decision", required=True),
            ParamSpec("reviewer", required=True),
            ParamSpec("note", default=""),
            _TENANT,
        ),
        envelope_keys=("status", "assignment_id", "decision", "projection"),
        surfaces=(
            SurfaceSpec(MCP, "review_execute"),
            SurfaceSpec(CLI, "review accept"),
            SurfaceSpec(HTTP, "/api/review/execute", "POST"),
            SurfaceSpec(SOCKET, "review_execute"),
        ),
    ),
    Operation(
        name="notebook_ask",
        family="research",
        description=(
            "Grounded question answering with cited sentences. Filters: "
            "code, min_confidence (high, medium, low), review_status "
            "(auto, review, confirmed, overridden), source, speaker, "
            "topic, or documents."
        ),
        handler="corpus_kb.tools.notebook_tools:notebook_ask_tool",
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
            SurfaceSpec(SOCKET, "notebook_ask"),
        ),
    ),
    Operation(
        name="notebook_evidence",
        family="research",
        description="Evidence for one code: ranked units with flags + conformal sizes.",
        handler="corpus_kb.tools.notebook_tools:notebook_evidence_tool",
        params=(
            ParamSpec("code", required=True),
            ParamSpec("project_id"),
            _TENANT,
            ParamSpec("version_id"),
            ParamSpec("limit", default=200),
        ),
        envelope_keys=(
            "status",
            "code",
            "code_id",
            "definition",
            "version_id",
            "n_units",
            "tabs",
        ),
        surfaces=(
            SurfaceSpec(MCP, "notebook_evidence"),
            SurfaceSpec(CLI, "research evidence"),
            SurfaceSpec(HTTP, "/api/notebook/evidence", "POST"),
            SurfaceSpec(SOCKET, "notebook_evidence"),
        ),
    ),
    Operation(
        name="notebook_uncoded",
        family="research",
        description="Uncoded units ranked by nearest-code margin (the missing-code radar).",
        handler="corpus_kb.tools.notebook_tools:notebook_uncoded_tool",
        params=(
            ParamSpec("project_id"),
            _TENANT,
            ParamSpec("version_id"),
            ParamSpec("k", default=25),
        ),
        envelope_keys=("status", "version_id", "units", "n_uncoded"),
        surfaces=(
            SurfaceSpec(MCP, "notebook_uncoded"),
            SurfaceSpec(CLI, "research uncoded"),
            SurfaceSpec(HTTP, "/api/notebook/uncoded", "POST"),
            SurfaceSpec(SOCKET, "notebook_uncoded"),
        ),
    ),
    Operation(
        name="notebook_overlap",
        family="research",
        description="Code overlap: flagged pairs, confusion, borderline-margin units.",
        handler="corpus_kb.tools.notebook_tools:notebook_overlap_tool",
        params=(
            ParamSpec("project_id"),
            _TENANT,
            ParamSpec("version_id"),
            ParamSpec("delta_amb", default=0.05),
        ),
        envelope_keys=(
            "status",
            "version_id",
            "tau_overlap",
            "matrix",
            "flagged_pairs",
            "borderline_units",
        ),
        surfaces=(
            SurfaceSpec(MCP, "notebook_overlap"),
            SurfaceSpec(CLI, "research overlap"),
            SurfaceSpec(HTTP, "/api/notebook/overlap", "POST"),
            SurfaceSpec(SOCKET, "notebook_overlap"),
        ),
    ),
    Operation(
        name="research_cycle",
        family="research",
        description=(
            "Run the research cycle. Long-running; synchronous on every "
            "surface. Promotion stays a human act at the halt gate."
        ),
        handler="corpus_kb.tools.cycle_tools:research_cycle",
        params=(
            ParamSpec("mode", default="out"),
            ParamSpec("project_id"),
            ParamSpec("ingest_dir"),
            ParamSpec("question"),
            ParamSpec("codebook_version_id"),
            ParamSpec("guide", default=False),
            ParamSpec("approve", default=False),
            ParamSpec("halt_on"),
            _TENANT,
        ),
        envelope_keys=("status", "exit_code", "gate", "checkpoint_kind", "resume_hint"),
        surfaces=(
            SurfaceSpec(MCP, "research_cycle"),
            SurfaceSpec(CLI, "research cycle"),
            SurfaceSpec(HTTP, "/api/research/cycle", "POST"),
            SurfaceSpec(SOCKET, "research_cycle"),
        ),
        exit_codes=(0, 2, 10, 11, 12, 13, 14, 15, 16, 17, 20, 21),
    ),
    Operation(
        name="research_demo",
        family="research",
        description=(
            "Narrated demo pipeline on the bundled demo corpus. "
            "Long-running; synchronous on every surface."
        ),
        handler="corpus_kb.tools.demo_tools:research_demo",
        params=(),
        envelope_keys=("status", "exit_code", "narration"),
        surfaces=(
            SurfaceSpec(MCP, "research_demo"),
            SurfaceSpec(CLI, "research demo"),
            SurfaceSpec(HTTP, "/api/research/demo", "POST"),
            SurfaceSpec(SOCKET, "research_demo"),
        ),
    ),
)

# Full surface parity: nothing is skipped. The tuple stays as the
# inventory's schema (empty list) so consumers never special-case absence.
# The demo-kb and doctor lifecycle commands gained their MCP legs and
# surface_registry rows in Task 5b (issue #176); they are registry rows,
# not catalogue rows — the catalogue stays the research/coding inventory.
SKIPPED_OPERATIONS: tuple[SkippedOperation, ...] = ()
