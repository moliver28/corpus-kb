"""The operation catalogue: the labeling family (analysis program W0).

``labeling_build``/``labeling_list``/``labeling_lfs`` are the
weak-supervision layer (WP-2): user-authored labeling functions scored
over the corpus, aggregated into probabilistic chunk labels. Wave 0
ships the registry rows and the honest envelopes; the engines land in
later waves, so every outcome on the skeleton is ``disabled`` (the
dormant default) or ``unavailable`` (flag on, engine not built).
Registry-first rule: change the row first, then the surfaces, then the
gates. All strings are plain English (ASD-STE100).
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
LABELING_ENVELOPE = ("status", "label_count", "labels")
LFS_ENVELOPE = ("status", "lf_count", "lfs")

LABELING_OPERATIONS: tuple[Operation, ...] = (
    Operation(
        name="labeling_build",
        family="labeling",
        description=(
            "Score the labeling functions over the corpus and write the "
            "aggregated chunk labels. Honest no-op while "
            "analysis.labeling.enabled is false."
        ),
        handler="corpus_kb.tools.labeling_tools:labeling_build",
        params=(_TENANT,),
        envelope_keys=LABELING_ENVELOPE,
        surfaces=(
            SurfaceSpec(MCP, "labeling_build"),
            SurfaceSpec(CLI, "labeling-build"),
            SurfaceSpec(HTTP, "/api/labeling/build", "POST"),
            SurfaceSpec(SOCKET, "labeling_build"),
        ),
        exit_codes=(0, 1, 2, 3),
    ),
    Operation(
        name="labeling_list",
        family="labeling",
        description="List the built chunk labels with probabilities, in a bounded window.",
        handler="corpus_kb.tools.labeling_tools:labeling_list",
        params=(ParamSpec("limit", default=50), _TENANT),
        envelope_keys=LABELING_ENVELOPE,
        surfaces=(
            SurfaceSpec(MCP, "labeling_list"),
            SurfaceSpec(CLI, "labeling-list"),
            SurfaceSpec(HTTP, "/api/labeling", "GET"),
            SurfaceSpec(SOCKET, "labeling_list"),
        ),
        exit_codes=(0, 1, 2, 3),
    ),
    Operation(
        name="labeling_lfs",
        family="labeling",
        description="List the registered labeling functions with kinds and coverage.",
        handler="corpus_kb.tools.labeling_tools:labeling_lfs",
        params=(ParamSpec("limit", default=50), _TENANT),
        envelope_keys=LFS_ENVELOPE,
        surfaces=(
            SurfaceSpec(MCP, "labeling_lfs"),
            SurfaceSpec(CLI, "labeling-lfs"),
            SurfaceSpec(HTTP, "/api/labeling/lfs", "GET"),
            SurfaceSpec(SOCKET, "labeling_lfs"),
        ),
        exit_codes=(0, 1, 2, 3),
    ),
)
