"""The operation catalogue: the extraction_tune family (Living Corpus W5).

One tune op over the confirmed corpus evidence: ``extraction_tune``
mines high-confidence relations and resolved review decisions into
tuning artifacts — profile seeds for ``customized`` extraction mode, or
a GLiNER LoRA adapter run. Registry-first rule: change the row first,
then the surfaces, then the gates. All strings are plain English
(ASD-STE100).
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

TUNE_ENVELOPE = (
    "status",
    "method",
    "profile",
    "dry_run",
    "examples_used",
    "exemplars_written",
    "seeds_path",
    "adapter_path",
    "steps",
    "notes",
)

EXTRACTION_TUNE_OPERATIONS: tuple[Operation, ...] = (
    Operation(
        name="extraction_tune",
        family="extraction_tune",
        description=(
            "Mine confirmed corpus decisions into tuning artifacts. "
            "Method exemplar writes profile seeds for customized mode; "
            "method lora trains a GLiNER LoRA adapter. dry_run previews."
        ),
        handler="corpus_kb.tools.extraction_tune_tools:extraction_tune",
        params=(
            _TENANT,
            # Issue #238: defaults live here, not in the adapters.
            ParamSpec("method", default="exemplar"),
            ParamSpec("profile"),
            ParamSpec("min_confidence", default=0.8),
            ParamSpec("limit", default=8),
            ParamSpec("dry_run", default=False),
        ),
        envelope_keys=TUNE_ENVELOPE,
        surfaces=(
            SurfaceSpec(MCP, "extraction_tune"),
            SurfaceSpec(CLI, "extraction-tune"),
            SurfaceSpec(HTTP, "/api/extraction-tune", "POST"),
            SurfaceSpec(SOCKET, "extraction_tune"),
        ),
        exit_codes=(0, 1, 2, 3),
    ),
)
