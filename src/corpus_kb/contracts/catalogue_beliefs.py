"""The operation catalogue: the beliefs family (Living Corpus W4).

Four ops over the deep Bayesian belief layer. Registry-first rule:
change the row first, then the surfaces, then the gates. All strings are
plain English (ASD-STE100).
"""

from __future__ import annotations

from corpus_kb.contracts.model import (
    CLI,
    DEFAULT_TENANT_ID,
    HTTP,
    MCP,
    SOCKET,
    Operation,
    ParamSpec,
    SurfaceSpec,
)

_TENANT = ParamSpec("tenant_id", default=DEFAULT_TENANT_ID)

CURRENT_ENVELOPE = ("status", "beliefs", "count", "rebuild")
TIMELINE_ENVELOPE = ("status", "belief_id", "eras", "posterior")
PREDICT_ENVELOPE = (
    "status",
    "subject",
    "relation",
    "object",
    "probability",
    "interval_low",
    "interval_high",
    "evidence_count",
)
COMPARE_ENVELOPE = ("status", "subject", "rows", "count")

BELIEFS_OPERATIONS: tuple[Operation, ...] = (
    Operation(
        name="beliefs_current",
        family="beliefs",
        description=(
            "Current belief posteriors (recomputed from the statements by "
            "default). Filters: subject, limit."
        ),
        handler="corpus_kb.tools.beliefs_tools:beliefs_current",
        params=(_TENANT, ParamSpec("subject"), ParamSpec("limit"), ParamSpec("rebuild")),
        envelope_keys=CURRENT_ENVELOPE,
        surfaces=(
            SurfaceSpec(MCP, "beliefs_current"),
            SurfaceSpec(CLI, "beliefs-current"),
            SurfaceSpec(HTTP, "/api/beliefs/current", "POST"),
            SurfaceSpec(SOCKET, "beliefs_current"),
        ),
        exit_codes=(0, 1, 2, 3),
    ),
    Operation(
        name="beliefs_timeline",
        family="beliefs",
        description=(
            "Era history for one belief: segmented stance moves with the exact posterior on top."
        ),
        handler="corpus_kb.tools.beliefs_tools:beliefs_timeline",
        params=(
            _TENANT,
            ParamSpec("subject"),
            ParamSpec("relation"),
            ParamSpec("object"),
        ),
        envelope_keys=TIMELINE_ENVELOPE,
        surfaces=(
            SurfaceSpec(MCP, "beliefs_timeline"),
            SurfaceSpec(CLI, "beliefs-timeline"),
            SurfaceSpec(HTTP, "/api/beliefs/timeline", "POST"),
            SurfaceSpec(SOCKET, "beliefs_timeline"),
        ),
        exit_codes=(0, 1, 2, 3),
    ),
    Operation(
        name="beliefs_predict",
        family="beliefs",
        description=(
            "Posterior predictive for one belief: the probability the next "
            "stance is positive, with its credible interval."
        ),
        handler="corpus_kb.tools.beliefs_tools:beliefs_predict",
        params=(
            _TENANT,
            ParamSpec("subject"),
            ParamSpec("relation"),
            ParamSpec("object"),
        ),
        envelope_keys=PREDICT_ENVELOPE,
        surfaces=(
            SurfaceSpec(MCP, "beliefs_predict"),
            SurfaceSpec(CLI, "beliefs-predict"),
            SurfaceSpec(HTTP, "/api/beliefs/predict", "POST"),
            SurfaceSpec(SOCKET, "beliefs_predict"),
        ),
        exit_codes=(0, 1, 2, 3),
    ),
    Operation(
        name="beliefs_compare",
        family="beliefs",
        description="Side-by-side belief posteriors for one subject across objects.",
        handler="corpus_kb.tools.beliefs_tools:beliefs_compare",
        params=(_TENANT, ParamSpec("subject")),
        envelope_keys=COMPARE_ENVELOPE,
        surfaces=(
            SurfaceSpec(MCP, "beliefs_compare"),
            SurfaceSpec(CLI, "beliefs-compare"),
            SurfaceSpec(HTTP, "/api/beliefs/compare", "POST"),
            SurfaceSpec(SOCKET, "beliefs_compare"),
        ),
        exit_codes=(0, 1, 2, 3),
    ),
)
