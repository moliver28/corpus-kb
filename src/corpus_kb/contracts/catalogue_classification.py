"""The operation catalogue: the classification family (analysis program W0).

``classification_build``/``classification_list`` are the few-shot
classification layer (WP-3): a seed label set fits a classifier over the
stored embeddings (tier ``head``) or the opt-in SetFit body (tier
``setfit``), with calibrated confidence and a review flag for low
confidence. Wave 0 ships the registry rows and the honest envelopes; the
engines land in later waves, so every outcome on the skeleton is
``disabled`` (the dormant default) or ``unavailable`` (flag on, engine
not built). Registry-first rule: change the row first, then the
surfaces, then the gates. All strings are plain English (ASD-STE100).
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

# Guaranteed-on-success keys only: the disabled/unavailable reasons stay
# off the tuple (the envelopes.py rule).
CLASSIFICATION_BUILD_ENVELOPE = (
    "status",
    "prediction_count",
    "class_count",
    "review_count",
    "params",
)
CLASSIFICATION_LIST_ENVELOPE = ("status", "prediction_count", "predictions")

CLASSIFICATION_OPERATIONS: tuple[Operation, ...] = (
    Operation(
        name="classification_build",
        family="classification",
        description=(
            "Fit the few-shot classifier against a seed label set and "
            "write the calibrated chunk predictions. Honest no-op while "
            "analysis.classification.enabled is false."
        ),
        handler="corpus_kb.tools.classification_tools:classification_build",
        params=(ParamSpec("seed_file", default=None), _TENANT),
        envelope_keys=CLASSIFICATION_BUILD_ENVELOPE,
        surfaces=(
            SurfaceSpec(MCP, "classification_build"),
            SurfaceSpec(CLI, "classification-build"),
            SurfaceSpec(HTTP, "/api/classification/build", "POST"),
            SurfaceSpec(SOCKET, "classification_build"),
        ),
        exit_codes=(0, 1, 2, 3),
    ),
    Operation(
        name="classification_list",
        family="classification",
        description="List the built chunk predictions, bounded by limit.",
        handler="corpus_kb.tools.classification_tools:classification_list",
        params=(ParamSpec("limit", default=50), _TENANT),
        envelope_keys=CLASSIFICATION_LIST_ENVELOPE,
        surfaces=(
            SurfaceSpec(MCP, "classification_list"),
            SurfaceSpec(CLI, "classification-list"),
            SurfaceSpec(HTTP, "/api/classification", "GET"),
            SurfaceSpec(SOCKET, "classification_list"),
        ),
        exit_codes=(0, 1, 2, 3),
    ),
)
