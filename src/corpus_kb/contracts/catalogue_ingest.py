"""Ingest-family operation rows (moved from ``catalogue.py`` for U3).

THE single source of truth for the cross-surface ingest operations. The
parity gate (``tests/test_surface_parity_deep.py``) and the inventory
command (``corpus-kb operations``) both iterate these rows, so the test
and the inventory can never diverge. ``contracts/__init__`` assembles the
final catalogue.

Registry-first rule (program standard 2): to add or change an operation,
change the row FIRST, then the surfaces, then run the gates. All strings
are plain English (ASD-STE100).
"""

from __future__ import annotations

from corpus_kb.contracts.envelopes import (
    AGGREGATE_EXTRA,
    DEDUP_KEYS,
    INGEST_ENVELOPE,
    TEXTBOOK_KEYS,
    TITLE_KEYS,
    TOKEN_KEYS,
    VALIDATION_KEYS,
)
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

# Additive HTTP/socket key sets for the ingest rows. The aggregate rows add
# the audit aggregate; the directory row wraps per-file envelopes instead.
_AGGREGATE_INGEST_EXTRA = (
    AGGREGATE_EXTRA | VALIDATION_KEYS | DEDUP_KEYS | TITLE_KEYS | TEXTBOOK_KEYS | TOKEN_KEYS
)
_INGEST_EXTRA = VALIDATION_KEYS | DEDUP_KEYS | TITLE_KEYS | TEXTBOOK_KEYS | TOKEN_KEYS

INGEST_OPERATIONS: tuple[Operation, ...] = (
    Operation(
        name="ingest_text",
        family="ingest",
        description="Ingest raw text with a source label and a document type.",
        handler="corpus_kb.tools.ingest_common:ingest_text",
        params=(
            ParamSpec("text", required=True),
            ParamSpec("source", default="raw_text"),
            ParamSpec("source_type", default="text"),
            # default None (Wave 6): an omitted validate rides the active
            # profile's value, then off. MCP injects only non-None catalogue
            # defaults, so a literal "off" here would defeat that fallback.
            ParamSpec("validate"),
            _TENANT,
        ),
        envelope_keys=INGEST_ENVELOPE,
        surfaces=(
            SurfaceSpec(MCP, "ingest_text"),
            SurfaceSpec(CLI, "ingest-text"),
            SurfaceSpec(HTTP, "/api/ingest/text", "POST"),
            SurfaceSpec(SOCKET, "ingest_text"),
        ),
        http_extra=_AGGREGATE_INGEST_EXTRA,
        socket_extra=_AGGREGATE_INGEST_EXTRA,
        exit_codes=(0, 1, 3),
    ),
    Operation(
        name="ingest_file",
        family="ingest",
        description="Ingest one file from disk; the document type is auto-detected.",
        handler="corpus_kb.tools.ingest_tools:ingest_file",
        params=(
            ParamSpec("file_path", required=True),
            ParamSpec("source_type"),
            ParamSpec("validate"),
            _TENANT,
        ),
        envelope_keys=INGEST_ENVELOPE,
        surfaces=(
            SurfaceSpec(MCP, "ingest_file"),
            SurfaceSpec(CLI, "ingest <path>"),
            SurfaceSpec(HTTP, "/api/ingest/file", "POST"),
            SurfaceSpec(SOCKET, "ingest_file"),
        ),
        http_extra=_AGGREGATE_INGEST_EXTRA,
        socket_extra=_AGGREGATE_INGEST_EXTRA,
        exit_codes=(0, 1, 2, 3),
    ),
    Operation(
        name="ingest_directory",
        family="ingest",
        description=(
            "Ingest the supported files under one directory in one walk. "
            "One filter: the WATCH_SUFFIXES modality table."
        ),
        handler="corpus_kb.tools.ingest_tools:ingest_directory",
        params=(
            ParamSpec("directory_path", required=True),
            ParamSpec("recursive", default=True),
            ParamSpec("source_type"),
            ParamSpec("validate"),
            _TENANT,
        ),
        envelope_keys=(
            "status",
            "directory",
            "files_processed",
            "total_documents",
            "total_entities",
            "results",
        ),
        surfaces=(
            SurfaceSpec(MCP, "ingest_directory"),
            # UI SPA Task 5a: the CLI route names the registered top-level
            # command (lockstep with surface_registry.cli_path, which the
            # command resolver test checks against the live Typer app).
            SurfaceSpec(CLI, "ingest"),
            SurfaceSpec(HTTP, "/api/ingest/directory", "POST"),
            SurfaceSpec(SOCKET, "ingest_directory"),
        ),
        http_extra=_INGEST_EXTRA,
        socket_extra=_INGEST_EXTRA,
        exit_codes=(0, 1, 2, 3),
    ),
    Operation(
        name="ingest_url",
        family="ingest",
        description="Fetch (or crawl) a website and ingest its HTML pages.",
        handler="corpus_kb.tools.ingest_url_tools:ingest_url",
        params=(
            ParamSpec("url", required=True),
            ParamSpec("crawl", default=False),
            ParamSpec("source_type"),
            ParamSpec("validate"),
            _TENANT,
        ),
        envelope_keys=("status", "pages", "total_documents"),
        http_extra=VALIDATION_KEYS | TOKEN_KEYS,
        socket_extra=VALIDATION_KEYS | TOKEN_KEYS,
        surfaces=(
            SurfaceSpec(MCP, "ingest_url"),
            SurfaceSpec(CLI, "ingest-url"),
            SurfaceSpec(HTTP, "/api/ingest/url", "POST"),
            SurfaceSpec(SOCKET, "ingest_url"),
        ),
        exit_codes=(0, 1, 2, 3),
    ),
    Operation(
        name="ingest_status",
        family="ingest",
        description="Recent ingest ledger entries plus degraded-chunk counts.",
        handler="corpus_kb.tools.ingest_surface_tools:ingest_status",
        params=(ParamSpec("limit", default=20), ParamSpec("project_id"), _TENANT),
        envelope_keys=("status", "ledger_present", "runs", "incomplete_runs", "degraded"),
        surfaces=(
            SurfaceSpec(MCP, "ingest_status"),
            SurfaceSpec(CLI, "ingest-status"),
            SurfaceSpec(HTTP, "/api/ingest/status", "GET"),
            SurfaceSpec(SOCKET, "ingest_status"),
        ),
        exit_codes=(0, 1, 2, 3),
    ),
    Operation(
        name="ingest_reembed",
        family="ingest",
        description="Re-embed degraded (zero-vector) and stale (NULL) chunks; honest counts.",
        handler="corpus_kb.tools.ingest_surface_tools:ingest_reembed",
        params=(ParamSpec("limit"), ParamSpec("doc_id"), _TENANT),
        envelope_keys=("status", "scanned", "fixed", "failed"),
        surfaces=(
            SurfaceSpec(MCP, "ingest_reembed"),
            SurfaceSpec(CLI, "ingest-reembed"),
            SurfaceSpec(HTTP, "/api/ingest/reembed", "POST"),
            SurfaceSpec(SOCKET, "ingest_reembed"),
        ),
        exit_codes=(0, 1, 2, 3),
    ),
    # Issue #202: one bulk re-embed for an embedding-model switch. It reads
    # the stored chunks (no partition/extract re-run), re-embeds them in
    # batches with the CURRENT config, and upserts the vectors. Resume is
    # the skip predicate: chunks already written by THIS model at THIS
    # width are counted, not re-done. The dry-run answers with counts only
    # (the fast size-estimate the owner runs first).
    Operation(
        name="ingest_reingest",
        family="ingest",
        description=(
            "Re-embed stored chunks with the current embedding model; "
            "dry-run plan, resume, one ledger row."
        ),
        handler="corpus_kb.tools.ingest_reingest:ingest_reingest",
        params=(
            ParamSpec("project_id"),
            ParamSpec("dry_run", default=False),
            ParamSpec("batch_size", default=128),
            _TENANT,
        ),
        envelope_keys=(
            "status",
            "dry_run",
            "target_model",
            "target_dimensions",
            "documents",
            "chunks",
            "embedded",
            "failed",
            "skipped_current",
        ),
        surfaces=(
            SurfaceSpec(MCP, "ingest_reingest"),
            SurfaceSpec(CLI, "ingest-reingest"),
            SurfaceSpec(HTTP, "/api/ingest/reingest", "POST"),
            SurfaceSpec(SOCKET, "ingest_reingest"),
        ),
        exit_codes=(0, 1, 2, 3),
    ),
)
