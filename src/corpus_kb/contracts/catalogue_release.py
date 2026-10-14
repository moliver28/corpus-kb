"""The operation catalogue: the release lifecycle family (U5, v6 section 6).

One handler per op in ``handlers/release_handlers.py``; the tool layer
(``tools/release_tools.py``) serializes each result with ``to_payload()``.
The canned parity stubs return exactly the key sets declared below.
"""

from __future__ import annotations

from corpus_kb.contracts.model import (
    CLI,
    DEFAULT_TENANT_ID,
    HTTP,
    MCP,
    Operation,
    ParamSpec,
    SurfaceSpec,
)

_TENANT = ParamSpec("tenant_id", default=DEFAULT_TENANT_ID)

RELEASE_OPERATIONS: tuple[Operation, ...] = (
    Operation(
        name="release_assess",
        family="release",
        description="Compose every release gate over current evidence; writes nothing.",
        handler="corpus_kb.handlers.release_handlers:assess_release",
        params=(
            ParamSpec("codebook_version_id", required=True),
            ParamSpec("profile"),
            ParamSpec("run_id"),
            _TENANT,
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
            ParamSpec("codebook_id"),
            ParamSpec("profile"),
            ParamSpec("release_id"),
            _TENANT,
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
            _TENANT,
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
            ParamSpec("waivers"),
            ParamSpec("run_id"),
            _TENANT,
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
        params=(ParamSpec("codebook_version_id", required=True), _TENANT),
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
            _TENANT,
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
)
