"""The operation catalogue: the chunk-coding family (issue #350).

The six chunk-coding routes the plugin reached only through hard-coded
HTTP posts (``/api/coding/*``), plus the two reads plugin 0.6.0 built by
hand (``coding_status`` from four SQL queries, ``coding_export`` from
hand-written SQL + JSON-to-CSV), each as ONE registry row with four
surfaces. The existing routes and request bodies stay byte-compatible:
the rows pin what already ships.

The shared function is the tool layer in ``tools.coding_tools``; the
pipeline itself is ``handlers.coding_handler.CodingHandler`` (status and
export bodies live in ``handlers.coding_status_ops``). Registry-first
rule: change the row first, then the surfaces, then the gates. All
strings are plain English (ASD-STE100).
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

CODING_OPERATIONS: tuple[Operation, ...] = (
    Operation(
        name="coding_embed_codebook",
        family="coding",
        description="Validate, embed, and load one codebook; answers its version id.",
        handler="corpus_kb.tools.coding_tools:coding_embed_codebook",
        params=(ParamSpec("codebook", required=True), _TENANT),
        envelope_keys=("status", "codes_embedded", "codebook_version_id"),
        surfaces=(
            SurfaceSpec(MCP, "coding_embed_codebook"),
            SurfaceSpec(CLI, "coding embed-codebook"),
            SurfaceSpec(HTTP, "/api/coding/embed-codebook", "POST"),
            SurfaceSpec(SOCKET, "coding_embed_codebook"),
        ),
        exit_codes=(0, 1, 3),
    ),
    Operation(
        name="coding_pool",
        family="coding",
        description="Run pooling (keyword hits, similarity) and coverage reconciliation.",
        handler="corpus_kb.tools.coding_tools:coding_pool",
        params=(ParamSpec("codebook_version_id", required=True), _TENANT),
        envelope_keys=(
            "status",
            "keyword_hits_written",
            "chunk_signals_written",
            "coverage",
            "keyword_governance",
            "speaker_roles",
        ),
        surfaces=(
            SurfaceSpec(MCP, "coding_pool"),
            SurfaceSpec(CLI, "coding pool"),
            SurfaceSpec(HTTP, "/api/coding/pool", "POST"),
            SurfaceSpec(SOCKET, "coding_pool"),
        ),
        exit_codes=(0, 1, 3),
    ),
    Operation(
        name="coding_code_batch",
        family="coding",
        description="Code a batch of (chunk, code) cells with confidence routing.",
        handler="corpus_kb.tools.coding_tools:coding_code_batch",
        params=(
            ParamSpec("cells", required=True),
            ParamSpec("coder"),
            ParamSpec("model"),
            ParamSpec("batch_id"),
            ParamSpec("coding_pass", default=1),
            ParamSpec("bands"),
            _TENANT,
        ),
        envelope_keys=("status", "cells_coded", "cells_rejected", "cells_routed", "results"),
        surfaces=(
            SurfaceSpec(MCP, "coding_code_batch"),
            SurfaceSpec(CLI, "coding code-batch"),
            SurfaceSpec(HTTP, "/api/coding/code-batch", "POST"),
            SurfaceSpec(SOCKET, "coding_code_batch"),
        ),
        exit_codes=(0, 1, 3),
    ),
    Operation(
        name="coding_reliability",
        family="coding",
        description="Sample the coded corpus and compute alpha/kappa reliability.",
        handler="corpus_kb.tools.coding_tools:coding_reliability",
        params=(
            ParamSpec("per_code_min", default=5),
            ParamSpec("seed", default=17),
            ParamSpec("code_universe"),
            _TENANT,
        ),
        envelope_keys=(
            "status",
            "coders",
            "n_sheets",
            "sampled_cells",
            "sampled_chunks",
            "insufficient_codes",
            "insufficient_sample",
            "overall",
            "per_code",
            "flags",
        ),
        surfaces=(
            SurfaceSpec(MCP, "coding_reliability"),
            SurfaceSpec(CLI, "coding reliability"),
            SurfaceSpec(HTTP, "/api/coding/reliability", "POST"),
            SurfaceSpec(SOCKET, "coding_reliability"),
        ),
        exit_codes=(0, 1, 3),
    ),
    Operation(
        name="coding_calibrate_floors",
        family="coding",
        description="Calibrate one code's pool/residual floors from labeled chunks.",
        handler="corpus_kb.tools.coding_tools:coding_calibrate_floors",
        params=(
            ParamSpec("code_id", required=True),
            ParamSpec("codebook_version_id", required=True),
            ParamSpec("positive_chunk_ids"),
            ParamSpec("negative_chunk_ids"),
            ParamSpec("min_pos", default=10),
            ParamSpec("global_fallback"),
            _TENANT,
        ),
        envelope_keys=(
            "status",
            "code_id",
            "pool_floor",
            "residual_floor",
            "n_positive_used",
            "n_negative_used",
            "n_positive_requested",
            "n_negative_requested",
            "fallback_applied",
        ),
        surfaces=(
            SurfaceSpec(MCP, "coding_calibrate_floors"),
            SurfaceSpec(CLI, "coding calibrate-floors"),
            SurfaceSpec(HTTP, "/api/coding/calibrate-floors", "POST"),
            SurfaceSpec(SOCKET, "coding_calibrate_floors"),
        ),
        exit_codes=(0, 1, 3),
    ),
    Operation(
        name="coding_saturation",
        family="coding",
        description="Report the ISR saturation signal and the batch stop rule.",
        handler="corpus_kb.tools.coding_tools:coding_saturation",
        params=(
            ParamSpec("batch_id"),
            ParamSpec("threshold", default=0.05),
            ParamSpec("min_samples", default=50),
            _TENANT,
        ),
        envelope_keys=(
            "status",
            "unique_codes",
            "total_applications",
            "isr",
            "batch_id",
            "new_codes",
            "base_unique",
            "should_stop",
        ),
        surfaces=(
            SurfaceSpec(MCP, "coding_saturation"),
            SurfaceSpec(CLI, "coding saturation"),
            SurfaceSpec(HTTP, "/api/coding/saturation", "POST"),
            SurfaceSpec(SOCKET, "coding_saturation"),
        ),
        exit_codes=(0, 1, 3),
    ),
    Operation(
        name="coding_status",
        family="coding",
        description="Corpus counts, saturation ratio, coverage by disposition, routes.",
        handler="corpus_kb.tools.coding_tools:coding_status",
        params=(_TENANT,),
        envelope_keys=("status", "documents", "chunks", "saturation", "coverage", "routes"),
        surfaces=(
            SurfaceSpec(MCP, "coding_status"),
            SurfaceSpec(CLI, "coding status"),
            SurfaceSpec(HTTP, "/api/coding/status", "GET"),
            SurfaceSpec(SOCKET, "coding_status"),
        ),
        exit_codes=(0, 1, 2, 3),
    ),
    Operation(
        name="coding_export",
        family="coding",
        description="Accepted codes joined with chunk text and document source, as rows.",
        handler="corpus_kb.tools.coding_tools:coding_export",
        params=(
            ParamSpec("limit", default=1000),
            ParamSpec("offset", default=0),
            _TENANT,
        ),
        envelope_keys=("status", "columns", "rows", "count"),
        surfaces=(
            SurfaceSpec(MCP, "coding_export"),
            SurfaceSpec(CLI, "coding export"),
            SurfaceSpec(HTTP, "/api/coding/export", "POST"),
            SurfaceSpec(SOCKET, "coding_export"),
        ),
        exit_codes=(0, 1, 2, 3),
    ),
)
