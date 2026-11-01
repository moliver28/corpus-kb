"""The operation catalogue: the labeling family (analysis program W0).

``labeling_build``/``labeling_list``/``labeling_lfs`` are the
weak-supervision layer (WP-2): user-authored labeling functions scored
over the corpus, aggregated into probabilistic chunk labels. Wave 0
ships the registry rows and the honest envelopes; the engines land in
later waves, so every outcome on the skeleton is ``disabled`` (the
dormant default) or ``unavailable`` (flag on, engine not built).
Registry-first rule: change the row first, then the surfaces, then the
gates. All strings are plain English (ASD-STE100).

The label-quality pair reads the SAME built labels:

  * ``labeling_triage`` — ranks labels whose assigned class has weak
    out-of-sample support into a human review queue (U39). Advisory
    only: the op never relabels anything.
  * ``labeling_gold_check`` — scores the built labels against a small
    gold dev set file through PPI (F6). Per class it reports the raw
    agreement and the corrected prevalence estimate with its interval.
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
TRIAGE_ENVELOPE = ("status", "n_flagged", "queue")
GOLD_ENVELOPE = ("status", "class_count", "validations")

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
    Operation(
        name="labeling_triage",
        family="labeling",
        description=(
            "Rank built labels with weak out-of-sample support into a "
            "human review queue. Advisory only; nothing is relabeled."
        ),
        handler="corpus_kb.tools.labeling_triage_tools:labeling_triage",
        params=(
            ParamSpec("cv_folds", default=5),
            ParamSpec("flag_threshold", default=0.2),
            ParamSpec("limit", default=50),
            _TENANT,
        ),
        envelope_keys=TRIAGE_ENVELOPE,
        surfaces=(
            SurfaceSpec(MCP, "labeling_triage"),
            SurfaceSpec(CLI, "labeling-triage"),
            SurfaceSpec(HTTP, "/api/labeling/triage", "POST"),
            SurfaceSpec(SOCKET, "labeling_triage"),
        ),
        exit_codes=(0, 1, 2, 3),
    ),
    Operation(
        name="labeling_gold_check",
        family="labeling",
        description=(
            "Score the built labels against a gold dev set file. Per "
            "class it reports the raw agreement and the PPI-corrected "
            "prevalence estimate."
        ),
        handler="corpus_kb.tools.labeling_gold_tools:labeling_gold_check",
        params=(
            ParamSpec("gold_file", default=None),
            ParamSpec("confidence_level", default=0.95),
            _TENANT,
        ),
        envelope_keys=GOLD_ENVELOPE,
        surfaces=(
            SurfaceSpec(MCP, "labeling_gold_check"),
            SurfaceSpec(CLI, "labeling-gold-check"),
            SurfaceSpec(HTTP, "/api/labeling/gold-check", "POST"),
            SurfaceSpec(SOCKET, "labeling_gold_check"),
        ),
        exit_codes=(0, 1, 2, 3),
    ),
)
