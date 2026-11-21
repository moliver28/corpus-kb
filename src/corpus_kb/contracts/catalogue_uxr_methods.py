"""The three wave-4 UXR method rows (methods 31-33).

``catalogue_analysis`` splices them into ``ANALYSIS_OPERATIONS`` before
``active_learn_next`` so the register order (31, 32, 33) holds. The
handlers live in ``tools/uxr_survey_codes.py`` and
``tools/uxr_method_tools.py``. All strings are plain English
(ASD-STE100). Registry-first rule: change the row first, then the
surfaces, then the gates.
"""

from __future__ import annotations

from corpus_kb.contracts.model import (
    CLI,
    HTTP,
    MCP,
    NLI_PREDICTIONS_ENVELOPE,
    PREDICTIONS_ENVELOPE,
    SOCKET,
    TENANT_PARAM,
    Operation,
    ParamSpec,
    SurfaceSpec,
)

SURVEY_CODES_ENVELOPE = ("status", "codes", "screened_count")

UXR_METHOD_OPERATIONS: tuple[Operation, ...] = (
    Operation(
        name="survey_openend_codes",
        family="analysis",
        description=(
            "Group open-ended survey answers per question into labeled "
            "clusters; report each code with its answer count. Register "
            "method 31."
        ),
        handler="corpus_kb.tools.uxr_survey_codes:survey_openend_codes",
        params=(
            ParamSpec("chunk_ids", default=None),
            ParamSpec("min_cluster_size", default=None),
            TENANT_PARAM,
        ),
        envelope_keys=SURVEY_CODES_ENVELOPE,
        surfaces=(
            SurfaceSpec(MCP, "survey_openend_codes"),
            SurfaceSpec(CLI, "survey-openend-codes"),
            SurfaceSpec(HTTP, "/api/analysis/survey-openend-codes", "POST"),
            SurfaceSpec(SOCKET, "survey_openend_codes"),
        ),
        exit_codes=(0, 1, 2, 3),
    ),
    Operation(
        name="usability_severity",
        family="analysis",
        description=(
            "Rate each finding from 0 to 4 with the fixed anchor rubric; "
            "unmatched rows route to review. Register method 32."
        ),
        handler="corpus_kb.tools.uxr_method_tools:usability_severity",
        params=(
            ParamSpec("chunk_ids", default=None),
            ParamSpec("limit", default=None),
            TENANT_PARAM,
        ),
        envelope_keys=PREDICTIONS_ENVELOPE,
        surfaces=(
            SurfaceSpec(MCP, "usability_severity"),
            SurfaceSpec(CLI, "usability-severity"),
            SurfaceSpec(HTTP, "/api/analysis/usability-severity", "POST"),
            SurfaceSpec(SOCKET, "usability_severity"),
        ),
        exit_codes=(0, 1, 2, 3),
    ),
    Operation(
        name="emotion_tag",
        family="analysis",
        description=(
            "Tag each chunk with one of eight fixed emotions through "
            "entailment judgments. Register method 33."
        ),
        handler="corpus_kb.tools.uxr_method_tools:emotion_tag",
        params=(
            ParamSpec("chunk_ids", default=None),
            ParamSpec("limit", default=50),
            ParamSpec("model", default=None),
            TENANT_PARAM,
        ),
        envelope_keys=NLI_PREDICTIONS_ENVELOPE,
        surfaces=(
            SurfaceSpec(MCP, "emotion_tag"),
            SurfaceSpec(CLI, "emotion-tag"),
            SurfaceSpec(HTTP, "/api/analysis/emotion-tag", "POST"),
            SurfaceSpec(SOCKET, "emotion_tag"),
        ),
        exit_codes=(0, 1, 2, 3),
    ),
)


def splice_uxr_methods(
    operations: tuple[Operation, ...],
) -> tuple[Operation, ...]:
    """Insert the UXR rows before ``active_learn_next`` (register order)."""
    at = next(
        (i for i, op in enumerate(operations) if op.name == "active_learn_next"),
        None,
    )
    if at is None:
        message = "active_learn_next missing from the analysis catalogue"
        raise ValueError(message)
    return operations[:at] + UXR_METHOD_OPERATIONS + operations[at:]
