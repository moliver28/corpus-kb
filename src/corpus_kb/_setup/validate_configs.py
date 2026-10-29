"""Config validation script for OpenCode CI enforcement.

Validates MCP configuration files across four editor formats:
  - OpenCode native (mcp key, command as array)
  - Claude Code (mcpServers key, command as string + args)
  - Cursor (mcpServers key, command as string + args, no autoApprove)
  - Codex (mcpServers key + autoApprove — copy-template for Codex CLI users)

Usage:
    python scripts/validate_configs.py

Exit code 0 = all valid, 1 = validation errors found.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import yaml

try:
    # Package context (installed checkout, tests).
    from corpus_kb._setup.validate_extraction_ml import (
        validate_extraction_ml as _validate_extraction_ml,
    )
except ImportError:  # pragma: no cover - script context (CI validate job)
    import importlib as _importlib

    _validate_extraction_ml = _importlib.import_module(
        "validate_extraction_ml"
    ).validate_extraction_ml

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Valid tool names for autoApprove lists. Two families:
#   * the master corpus-kb MCP tool set (search / sql / graph / metadata),
#   * the research/coding surfaces (tools/research_tools.py +
#     tools/notebook_tools.py — todo-18 tool-name lockstep, r9: every PR
#     registering an MCP tool extends this set AND regenerates the harness
#     pack in the SAME PR).
VALID_TOOL_NAMES: frozenset[str] = frozenset(
    {
        # -- master corpus-kb MCP tools -------------------------------------
        "search",
        "ask",
        "search_context",
        "search_similar",
        "retrieve_context",
        "list_documents",
        "get_stats",
        "list_versions",
        "list_branches",
        "get_entity_relations",
        "search_graph",
        "graph_neighbors",
        "graph_traverse",
        "sql_query",
        "sql_tables",
        "get_document_tags",
        "get_metadata",
        "query_document_stats",
        "sync_database",
        # -- research/coding surfaces (todos 11-18) --------------------------
        "research_ingest_transcript",
        "research_ingest",
        "coding_run",
        "inductive_run",
        "codebook_promote",
        "research_report",
        "review_execute",
        "notebook_ask",
        "notebook_evidence",
        "notebook_uncoded",
        "notebook_overlap",
        "research_cycle",
        "research_demo",
        # -- release lifecycle (U5, tools/release_tools.py, P7) --------------
        "release_assess",
        "release_request",
        "release_review",
        "release_execute",
        "release_status",
        "release_compare",
        # -- ingest front door (Stage 5, tools/ingest_surface_tools.py) ------
        "ingest_status",
        "ingest_reembed",
        # -- pipeline ingest ops (UI SPA Task 5a): catalogue-registered MCP
        #    tools that gained surface_registry rows (manifest longRunning) --
        "ingest_directory",
        "ingest_url",
        # -- lifecycle UI ops (UI SPA Task 5b, issue #176): registry rows over
        #    the demo loader and the doctor probes ---------------------------
        "demo_load",
        "doctor",
        # -- lifecycle read lists (UI SPA Task 5b, spec B2): the Review and
        #    Codebook screens' GET reads -------------------------------------
        "projects_list",
        "codebooks_list",
        "codebook_proposals",
        "coding_assignments",
        # -- topics layer (Wave 4, tools/topics_tools.py) ---------------------
        "topics_build",
        "topics_list",
        # -- analysis program (Wave 0 skeleton, tools/*_tools.py) -------------
        "topics_trends",
        "labeling_build",
        "labeling_list",
        "labeling_lfs",
        "classification_build",
        "classification_list",
        "analysis_backfill_dates",
        "analyze_run",
        # -- analysis method register program (wave 2, tools/*_tools.py) -----
        "analysis_select_method",
        "keyphrases_extract",
        "lexical_classify",
        "neighbors_classify",
        "centroids_classify",
        "clusters_inspect",
        "duplicates_screen",
        "outliers_rank",
        "nli_classify",
        "contradictions_screen",
        "sentiment_screen",
        "active_learn_next",
    }
)

# ---------------------------------------------------------------------------
# OpenCode native format validator
# ---------------------------------------------------------------------------


def validate_opencode_config(config: dict[str, Any]) -> list[str]:
    """Validate an OpenCode native format config (mcp key).

    Required structure:
        {
            "$schema": "https://opencode.ai/config.json",
            "mcp": {
                "<tool-name>": {
                    "type": "local",
                    "description": "...",
                    "command": ["executable", "arg1", ...],   # MUST be array
                    "environment": {},
                    "autoApprove": ["tool1", "tool2", ...],   # MUST be non-empty list
                }
            }
        }

    Returns a list of error strings (empty = valid).
    """
    errors: list[str] = []

    if not isinstance(config, dict):
        return ["Config must be a JSON object"]

    # $schema
    if "$schema" not in config:
        errors.append("Missing required '$schema' field")

    # mcp key (not mcpServers)
    if "mcp" not in config:
        errors.append("Missing required 'mcp' key — OpenCode format uses 'mcp', not 'mcpServers'")
        return errors

    if "mcpServers" in config:
        errors.append("Found 'mcpServers' key — this is the old format; use 'mcp' for OpenCode")

    mcp = config["mcp"]
    if not isinstance(mcp, dict) or not mcp:
        errors.append("'mcp' must be a non-empty object mapping tool names to config")
        return errors

    for tool_name, tool_config in mcp.items():
        prefix = f"mcp.{tool_name}"
        errors.extend(_validate_opencode_tool(prefix, tool_config))

    return errors


def _validate_opencode_tool(prefix: str, tool_config: dict[str, Any]) -> list[str]:
    """Validate a single tool entry within the OpenCode mcp block."""
    errors: list[str] = []

    if not isinstance(tool_config, dict):
        return [f"{prefix}: tool config must be an object"]

    # Required fields
    for field in ("type", "description", "command", "environment", "autoApprove"):
        if field not in tool_config:
            errors.append(f"{prefix}: missing required field '{field}'")

    # command must be an array
    command = tool_config.get("command")
    if command is not None:
        if not isinstance(command, list):
            errors.append(
                f"{prefix}: 'command' must be an array of strings "
                f"(e.g. ['corpus-kb', '--transport', 'stdio']), "
                f"got {type(command).__name__}"
            )
        elif not command:
            errors.append(f"{prefix}: 'command' array must not be empty")

    # autoApprove must be a non-empty list of valid tool names
    auto_approve = tool_config.get("autoApprove")
    if auto_approve is not None:
        if not isinstance(auto_approve, list):
            errors.append(
                f"{prefix}: 'autoApprove' must be a list of tool names, "
                f"got {type(auto_approve).__name__}"
            )
        elif len(auto_approve) == 0:
            errors.append(f"{prefix}: 'autoApprove' must not be empty")
        else:
            for tool in auto_approve:
                if tool not in VALID_TOOL_NAMES:
                    errors.append(
                        f"{prefix}: invalid tool name in autoApprove: '{tool}' — "
                        f"must be one of: {', '.join(sorted(VALID_TOOL_NAMES))}"
                    )

    return errors


# ---------------------------------------------------------------------------
# Claude Code format validator
# ---------------------------------------------------------------------------


def validate_claude_config(config: dict[str, Any]) -> list[str]:
    """Validate a Claude Code format config (mcpServers key).

    Required structure:
        {
            "mcpServers": {
                "<tool-name>": {
                    "name": "...",
                    "description": "...",
                    "command": "executable",          # MUST be string
                    "args": ["arg1", ...],
                    "env": {},
                    "autoApprove": ["tool1", ...],    # REQUIRED for Claude
                }
            }
        }

    Returns a list of error strings (empty = valid).
    """
    errors: list[str] = []

    if not isinstance(config, dict):
        return ["Config must be a JSON object"]

    if "mcpServers" not in config:
        errors.append("Missing required 'mcpServers' key for Claude Code format")
        return errors

    servers = config["mcpServers"]
    if not isinstance(servers, dict) or not servers:
        errors.append("'mcpServers' must be a non-empty object")
        return errors

    for tool_name, tool_config in servers.items():
        prefix = f"mcpServers.{tool_name}"
        errors.extend(_validate_claude_tool(prefix, tool_config))

    return errors


def _validate_claude_tool(prefix: str, tool_config: dict[str, Any]) -> list[str]:
    """Validate a single tool entry within Claude Code mcpServers."""
    errors: list[str] = []

    if not isinstance(tool_config, dict):
        return [f"{prefix}: tool config must be an object"]

    for field in ("name", "description", "command", "args", "autoApprove"):
        if field not in tool_config:
            errors.append(f"{prefix}: missing required field '{field}'")

    # command must be a string
    command = tool_config.get("command")
    if command is not None and not isinstance(command, str):
        errors.append(
            f"{prefix}: 'command' must be a string (e.g. 'corpus-kb'), got {type(command).__name__}"
        )

    # autoApprove validation (same as OpenCode)
    auto_approve = tool_config.get("autoApprove")
    if auto_approve is not None:
        if not isinstance(auto_approve, list):
            errors.append(
                f"{prefix}: 'autoApprove' must be a list of tool names, "
                f"got {type(auto_approve).__name__}"
            )
        elif len(auto_approve) == 0:
            errors.append(f"{prefix}: 'autoApprove' must not be empty")
        else:
            for tool in auto_approve:
                if tool not in VALID_TOOL_NAMES:
                    errors.append(f"{prefix}: invalid tool name in autoApprove: '{tool}'")

    return errors


# ---------------------------------------------------------------------------
# Cursor format validator
# ---------------------------------------------------------------------------


def validate_cursor_config(config: dict[str, Any]) -> list[str]:
    """Validate a Cursor format config (mcpServers key, no autoApprove).

    Required structure:
        {
            "mcpServers": {
                "<tool-name>": {
                    "name": "...",
                    "description": "...",
                    "command": "executable",          # MUST be string
                    "args": ["arg1", ...],
                    "env": {},
                    # autoApprove is NOT used by Cursor
                }
            }
        }

    Returns a list of error strings (empty = valid).
    """
    errors: list[str] = []

    if not isinstance(config, dict):
        return ["Config must be a JSON object"]

    if "mcpServers" not in config:
        errors.append("Missing required 'mcpServers' key for Cursor format")
        return errors

    servers = config["mcpServers"]
    if not isinstance(servers, dict) or not servers:
        errors.append("'mcpServers' must be a non-empty object")
        return errors

    for tool_name, tool_config in servers.items():
        prefix = f"mcpServers.{tool_name}"
        errors.extend(_validate_cursor_tool(prefix, tool_config))

    return errors


def _validate_cursor_tool(prefix: str, tool_config: dict[str, Any]) -> list[str]:
    """Validate a single tool entry within Cursor mcpServers."""
    errors: list[str] = []

    if not isinstance(tool_config, dict):
        return [f"{prefix}: tool config must be an object"]

    # Cursor requires: name, description, command, args, env
    # autoApprove is NOT required (Cursor doesn't use it)
    for field in ("name", "description", "command", "args", "env"):
        if field not in tool_config:
            errors.append(f"{prefix}: missing required field '{field}'")

    # command must be a string
    command = tool_config.get("command")
    if command is not None and not isinstance(command, str):
        errors.append(
            f"{prefix}: 'command' must be a string (e.g. 'corpus-kb'), got {type(command).__name__}"
        )

    return errors


# ---------------------------------------------------------------------------
# Cross-config consistency checks
# ---------------------------------------------------------------------------


def _extract_tool_set(config: dict[str, Any], key: str) -> dict[str, str]:
    """{tool_name: description} from one editor config under ``key``."""
    return {
        name: cfg.get("description", "")
        for name, cfg in config[key].items()
        if isinstance(cfg, dict)
    }


def _tool_set_mismatches(tool_sets: dict[str, dict[str, str]]) -> list[str]:
    """Errors for configs that define a different tool-name set than the first."""
    errors: list[str] = []
    all_tool_names = [set(ts.keys()) for ts in tool_sets.values()]
    reference = all_tool_names[0]
    for fmt, names in zip(tool_sets.keys(), all_tool_names[1:], strict=False):
        if names != reference:
            missing = reference - names
            extra = names - reference
            parts = []
            if missing:
                parts.append(f"missing: {', '.join(sorted(missing))}")
            if extra:
                parts.append(f"extra: {', '.join(sorted(extra))}")
            errors.append(
                f"Cross-config tool mismatch: all configs must define the same set of tools. "
                f"Compared to first config, '{fmt}' has {'; '.join(parts)}"
            )
    return errors


def _description_mismatches(tool_sets: dict[str, dict[str, str]]) -> list[str]:
    """Errors for tools whose description differs across configs."""
    errors: list[str] = []
    all_tool_names = [set(ts.keys()) for ts in tool_sets.values()]
    common_tools = set.intersection(*all_tool_names) if all_tool_names else set()
    formats = list(tool_sets.keys())
    for tool in sorted(common_tools):
        descriptions = {fmt: tool_sets[fmt][tool] for fmt in formats}
        unique_descs = set(descriptions.values())
        if len(unique_descs) > 1:
            desc_details = "; ".join(f"{fmt}: '{desc}'" for fmt, desc in descriptions.items())
            errors.append(f"Description mismatch for tool '{tool}': {desc_details}")
    return errors


def check_cross_config_consistency(
    opencode_config: dict[str, Any] | None,
    claude_config: dict[str, Any] | None,
    cursor_config: dict[str, Any] | None,
    codex_config: dict[str, Any] | None = None,
) -> list[str]:
    """Check that all configs define the same tools with matching descriptions.

    Returns a list of error strings (empty = consistent).
    """
    errors: list[str] = []

    # Extract tool sets from each config: format -> {tool_name: description}
    sources = (
        ("opencode", opencode_config, "mcp"),
        ("claude", claude_config, "mcpServers"),
        ("cursor", cursor_config, "mcpServers"),
        ("codex", codex_config, "mcpServers"),
    )
    tool_sets = {
        name: _extract_tool_set(cfg, key) for name, cfg, key in sources if cfg and key in cfg
    }

    if len(tool_sets) < 2:
        return errors  # Nothing to cross-check

    return _tool_set_mismatches(tool_sets) + _description_mismatches(tool_sets)


# ---------------------------------------------------------------------------
# Runtime config.yaml — extraction.gliner block
# ---------------------------------------------------------------------------

# The six mention-level types gliner_backend.py can emit (must stay in
# lockstep with extraction.gliner_backend.TYPE_DEFINITIONS).
GLINER_MENTION_TYPES: frozenset[str] = frozenset(
    {"person", "organization", "location", "date", "money", "product"}
)


def validate_statements_block(config: dict[str, Any]) -> list[str]:
    """Validate the ``statements`` block (Living Corpus W2 atomization)."""
    errors: list[str] = []
    statements = config.get("statements")
    if statements is None:
        return errors
    if not isinstance(statements, dict):
        return [f"statements: must be a mapping, got {type(statements).__name__}"]
    enabled = statements.get("enabled")
    if not isinstance(enabled, bool):
        errors.append(f"statements.enabled: must be a boolean, got {type(enabled).__name__}")
    templates_dir = statements.get("templates_dir")
    if templates_dir is not None and not isinstance(templates_dir, str):
        errors.append("statements.templates_dir: must be a string path")
    max_per_chunk = statements.get("max_per_chunk")
    if max_per_chunk is not None and (not isinstance(max_per_chunk, int) or max_per_chunk < 1):
        errors.append("statements.max_per_chunk: must be an integer >= 1")
    return errors


def load_yaml(path: Path) -> dict[str, Any]:
    """Load a YAML file, returning {} for empty or non-mapping documents."""
    with path.open(encoding="utf-8") as f:
        loaded: object = yaml.safe_load(f)
    return loaded if isinstance(loaded, dict) else {}


def validate_extraction_gliner(config: dict[str, Any]) -> list[str]:
    """Validate the ``extraction.gliner`` block of a runtime config.

    The block (and the whole ``extraction`` key) is optional; when
    present, every key is checked so a typo can never silently disable
    the opt-in GLiNER2 backend.

    Returns a list of error strings (empty = valid).
    """
    errors: list[str] = []

    extraction = config.get("extraction")
    if extraction is None:
        return errors
    if not isinstance(extraction, dict):
        return [f"extraction: must be a mapping, got {type(extraction).__name__}"]

    gliner = extraction.get("gliner")
    if gliner is None:
        return errors
    if not isinstance(gliner, dict):
        return [f"extraction.gliner: must be a mapping, got {type(gliner).__name__}"]

    enabled = gliner.get("enabled")
    if not isinstance(enabled, bool):
        errors.append(f"extraction.gliner.enabled: must be a boolean, got {type(enabled).__name__}")

    model = gliner.get("model")
    if not isinstance(model, str) or not model:
        errors.append("extraction.gliner.model: must be a non-empty string")

    revision = gliner.get("revision")
    if not isinstance(revision, str) or not revision:
        errors.append("extraction.gliner.revision: must be a non-empty string")

    types = gliner.get("types")
    if not isinstance(types, list) or not types:
        errors.append("extraction.gliner.types: must be a non-empty list")
    else:
        for mention_type in types:
            if not isinstance(mention_type, str) or mention_type not in GLINER_MENTION_TYPES:
                errors.append(
                    f"extraction.gliner.types: unknown mention type {mention_type!r} — "
                    f"must be one of: {', '.join(sorted(GLINER_MENTION_TYPES))}"
                )

    return errors


def validate_extraction_ml(config: dict[str, Any]) -> list[str]:
    """Validate the ``extraction.mode``/``relex``/``ensemble``/``cache`` keys.

    Thin re-export: the checks live in ``validate_extraction_ml.py`` (the
    block outgrew this module's complexity gate). Kept importable from here
    for the historical ``validate_configs.<name>`` surface.
    """
    return _validate_extraction_ml(config)


def validate_ingestion_block(config: dict[str, Any]) -> list[str]:
    """Validate the ``ingestion`` block of a runtime config.

    The block is optional; when present, ``strategies`` must map source
    types to non-empty strategy-name strings and ``default`` must be a
    non-empty string — the registry resolves these names at ingest time, so
    a mistyped value must fail validation instead of silently falling back.

    Returns a list of error strings (empty = valid).
    """
    errors: list[str] = []

    ingestion = config.get("ingestion")
    if ingestion is None:
        return errors
    if not isinstance(ingestion, dict):
        return [f"ingestion: must be a mapping, got {type(ingestion).__name__}"]

    strategies = ingestion.get("strategies")
    if not isinstance(strategies, dict):
        errors.append(
            f"ingestion.strategies: must be a mapping of source type -> strategy name, "
            f"got {type(strategies).__name__}"
        )
    else:
        for source_type, name in strategies.items():
            if not isinstance(source_type, str) or not source_type:
                errors.append(
                    f"ingestion.strategies: keys must be non-empty source-type strings, "
                    f"got {source_type!r}"
                )
            if not isinstance(name, str) or not name:
                errors.append(
                    f"ingestion.strategies[{source_type!r}]: must be a non-empty "
                    f"strategy-name string, got {name!r}"
                )

    default = ingestion.get("default")
    if not isinstance(default, str) or not default:
        errors.append("ingestion.default: must be a non-empty string")

    tokenizer = ingestion.get("tokenizer")
    if tokenizer is not None and tokenizer != "semchunk":
        errors.append(
            f"ingestion.tokenizer: unknown value {tokenizer!r} — must be 'semchunk' "
            "(the key stays absent for the default chars/4 split)"
        )

    dedup = ingestion.get("dedup")
    if dedup is not None and dedup != "minhash":
        errors.append(
            f"ingestion.dedup: unknown value {dedup!r} — must be 'minhash' "
            "(the key stays absent when dedup is off)"
        )

    threshold = ingestion.get("dedup_threshold")
    if threshold is not None:
        if not isinstance(threshold, (int, float)) or isinstance(threshold, bool):
            errors.append("ingestion.dedup_threshold: must be a number")
        elif not 0.0 < float(threshold) <= 1.0:
            errors.append("ingestion.dedup_threshold: must be in (0, 1]")

    return errors


# ---------------------------------------------------------------------------
# Runtime config.yaml key validation (plan §1: "validate_configs.py learns
# every new key"). Each stage extends RUNTIME_CONFIG_KEYS for the keys it adds.
# ---------------------------------------------------------------------------

# dotted key -> expected YAML type (bool/str/int/float/list/dict)
RUNTIME_CONFIG_KEYS: dict[str, type] = {
    # extraction.backend is the PREFERRED extraction-backend key
    # (graph.extractor is the accepted legacy alias — see
    # corpus_kb.extraction.extraction_backend_name).
    "extraction.backend": str,
    "embedding.mrl_trained": bool,
    "embedding.late_chunking": bool,
    "embedding.query_instruction": bool,
    "storage.vector_type": str,
    "ingestion.default": str,
    "ingestion.strategies": dict,
    "ingestion.concurrency": int,
    # ingestion.tokenizer / ingestion.dedup / ingestion.dedup_threshold are
    # NOT here: absent is the shipped default (opt-in gates), so the
    # when-present enum + range checks live in validate_ingestion_block
    # (the reranking.backend pattern).
    # -- ingest.chunking (canonical home of the hierarchy knobs) -----------
    "ingest.chunking.enabled": bool,
    "ingest.chunking.child_tokens": int,
    "ingest.chunking.chunk_tokens": int,
    "ingest.chunking.parent_tokens": int,
    "ingest.chunking.overlap_tokens": int,
    "ingest.chunking.respect_boundaries": list,
    # -- ingest.contextualize (renamed from contextual.*) ------------------
    "ingest.contextualize.enabled": bool,
    # W3 contextualize v2 keys (rag/contextual_settings.py owns the rules).
    "ingest.contextualize.backend": str,
    "ingest.contextualize.model": str,
    "ingest.contextualize.prompt_profile": str,
    "ingest.contextualize.max_context_tokens": int,
    "ingest.contextualize.document_first_prompt": bool,
    "ingest.contextualize.apply_to": list,
    # -- retrieval (renamed from search.*) ----------------------------------
    "retrieval.mode": str,
    # Storage-portability keys (docs/research/storage-portability-program.md
    # §3): the DuckDB-side search knobs. Postgres ignores both.
    "retrieval.vector_index": str,
    "retrieval.fts_rebuild_debounce_s": float,
    "retrieval.vector_candidates": int,
    "retrieval.lexical_candidates": int,
    "retrieval.rrf_k": int,
    "retrieval.expand_to_parent": bool,
    "retrieval.hnsw.m": int,
    "retrieval.hnsw.ef_construction": int,
    # -- reranking (renamed from search.rerank.*) ---------------------------
    "reranking.enabled": bool,
    "reranking.return_top_k": int,
    # reranking.backend is NOT here: null is the shipped default, so the
    # null-or-enum union is enforced by _reranking_block_errors below.
    "reranking.llamacpp_url": str,
    # -- website (URL) ingestion (src/corpus_kb/web/, WebFetchConfig) -------
    "web.user_agent": str,
    "web.timeout_seconds": int,
    "web.max_bytes": int,
    "web.respect_robots": bool,
    "web.max_pages": int,
    "web.max_depth": int,
    "web.delay_seconds": float,
    "web.allow_private_addresses": bool,
    # -- topics (Wave 4, src/corpus_kb/topics/) ------------------------------
    # min_topic_size is str|int ("auto" or a positive int): the union is
    # enforced by _topics_block_errors below, not by the single-type map.
    "topics.enabled": bool,
    "topics.method": str,
    "topics.llm_reduction": bool,
    "topics.llm": str,
    "topics.llm_base_url": str,
    # -- server (HTTP adapter gates) -----------------------------------------
    "server.openai_compat": bool,
    # -- database (storage-portability program §3) ----------------------------
    "database.backend": str,
    # -- graph traversal ------------------------------------------------------
    "graph.max_hops": int,
    # DuckDB graph traversal engine (textbook-ingestion Wave 1; Postgres
    # ignores the key). The when-present enum lives in the cross-field
    # rules (SUPPORTED_DUCKDB_GRAPH_BACKENDS).
    "graph.duckdb_graph_backend": str,
    # -- textbook ingestion (textbook-ingestion program, Wave 1) -------------
    "textbook.min_pages": int,
    "textbook.window_pages": int,
    # textbook.parser enum: SUPPORTED_TEXTBOOK_PARSERS (cross-field rules).
    "textbook.parser": str,
    "textbook.cache_dir": str,
    # -- ingest queue worker knobs (tools/queue_tools.py, Wave 1) ------------
    "queue.autorun": bool,
    "queue.poll_seconds": int,
    "queue.stale_lease_seconds": int,
    "queue.max_attempts": int,
    # -- graph_policy (Wave 1; enum in the cross-field rules) -----------------
    "graph_policy.mode": str,
    # -- ui (UI SPA program Task 3, src/corpus_kb/ui/spa.py) ----------------
    "ui.spa.poll_ms": int,
    "ui.spa.request_timeout_s": float,
    "ui.spa.max_upload_mb": int,
    # -- notebooks (UX program Wave 6, src/corpus_kb/notebooks/) -------------
    "notebooks.enabled": bool,
    "notebooks.directory": str,
    # -- observability (UX program Wave 4, src/corpus_kb/utils/tracing.py) ---
    "observability.enabled": bool,
    "observability.otlp_endpoint": str,
    "observability.service_name": str,
}

SUPPORTED_RETRIEVAL_MODES = frozenset({"hybrid_rrf"})
SUPPORTED_VECTOR_TYPES = frozenset({"halfvec"})
# database.backend values (storage-portability program §3).
SUPPORTED_DATABASE_BACKENDS = frozenset({"postgres", "duckdb"})
# retrieval.vector_index values: the DuckDB-side search mode. Postgres
# keeps its pgvector HNSW and ignores the key.
SUPPORTED_VECTOR_INDEX_MODES = frozenset({"exact_scan", "hnsw"})
# graph.max_hops: traversal depth cap. The ceiling is today's AGE cap.
MAX_HOPS_MIN = 1
MAX_HOPS_CEILING = 25
# Backend-mix refusals (program §11 rule 15 string bank, VERBATIM). This
# module cannot import corpus_kb (the CI validate-configs job installs only
# PyYAML), so corpus_kb.config mirrors this logic; a lockstep test pins the
# equality of the two renderings.
AGE_ON_DUCKDB_ERROR = "Set database.backend to postgres, or remove the graph.backend setting."
PGML_EMBEDDER_ON_DUCKDB_ERROR = (
    "PostgresML runs only on Postgres. The Ollama embedder will be used instead."
)
PGML_RERANKER_ON_DUCKDB_ERROR = (
    "PostgresML reranking runs only on Postgres. The Ollama reranker will be used instead."
)
PGML_EXTRACTION_ON_DUCKDB_ERROR = (
    "PostgresML extraction runs only on Postgres. The regex extractor will be used instead."
)
SUPPORTED_CONTEXTUAL_PROFILES = frozenset({"interview", "generic"})
SUPPORTED_APPLY_TO_TARGETS = frozenset({"embedding", "lexical"})
SUPPORTED_TOPIC_METHODS = frozenset({"bertopic"})
# reranking.backend values (null also valid — the qwen3 judgment scorer).
# "generative" is the explicit alias of null; "fake" is the test double.
SUPPORTED_RERANK_BACKENDS = frozenset({"llamacpp", "generative", "fake"})
RETURN_TOP_K_MIN = 1
RETURN_TOP_K_MAX = 1000
# textbook.parser values (textbook-ingestion Wave 1). "docling" is the
# structured page parser ([docling] extra); "pypdf" the scan-only fallback
# ([scan] extra).
SUPPORTED_TEXTBOOK_PARSERS = frozenset({"docling", "pypdf"})
# graph_policy.mode values (Wave 1): "all" mirrors today's write behavior;
# "semantic_only" admits only graph_eligible relation types.
SUPPORTED_GRAPH_POLICY_MODES = frozenset({"all", "semantic_only"})
# graph.duckdb_graph_backend values (DuckDB graph traversal engine).
SUPPORTED_DUCKDB_GRAPH_BACKENDS = frozenset({"cte", "duckpgq"})


def _contextualize_block_errors(contextualize: dict[str, Any]) -> list[str]:
    """Enum + shape rules for the W3 ``ingest.contextualize`` keys."""
    errors: list[str] = []
    if "backend" in contextualize:
        backend = contextualize.get("backend")
        if backend != "ollama":
            errors.append(
                f"ingest.contextualize.backend: must be 'ollama' when present, got {backend!r}"
            )
    profile = contextualize.get("prompt_profile")
    if profile is not None and profile not in SUPPORTED_CONTEXTUAL_PROFILES:
        profiles = ", ".join(sorted(SUPPORTED_CONTEXTUAL_PROFILES))
        errors.append(
            f"ingest.contextualize.prompt_profile: must be one of ({profiles}), got {profile!r}"
        )
    errors.extend(_apply_to_errors(contextualize.get("apply_to")))
    return errors


def _apply_to_errors(apply_to: object) -> list[str]:
    """Non-empty subset-of-{embedding, lexical} rule for ``apply_to``."""
    if apply_to is None:
        return []
    if not isinstance(apply_to, list) or not apply_to:
        return [f"ingest.contextualize.apply_to: must be a non-empty list, got {apply_to!r}"]
    unknown = [t for t in apply_to if t not in SUPPORTED_APPLY_TO_TARGETS]
    if not unknown:
        return []
    targets = ", ".join(sorted(SUPPORTED_APPLY_TO_TARGETS))
    return [
        f"ingest.contextualize.apply_to: unknown target(s) {unknown!r}; "
        f"supported targets: {targets}"
    ]


def _topics_block_errors(topics: dict[str, Any]) -> list[str]:
    """Enum + union-type rules for the Wave 4 ``topics`` keys."""
    errors: list[str] = []
    if "method" in topics:
        method = topics.get("method")
        if method not in SUPPORTED_TOPIC_METHODS:
            errors.append(
                f"topics.method: must be one of {sorted(SUPPORTED_TOPIC_METHODS)} "
                f"when present, got {method!r}"
            )
    # Presence + union type: "auto" or a positive int (bool is an int
    # subclass — reject it the way the int keys do).
    if "min_topic_size" not in topics:
        errors.append(
            "missing required key 'topics.min_topic_size' "
            "(expected str or int: 'auto' or a positive int)"
        )
    else:
        size = topics.get("min_topic_size")
        size_ok = (isinstance(size, str) and size == "auto") or (
            not isinstance(size, bool) and isinstance(size, int) and size > 0
        )
        if not size_ok:
            errors.append(
                "key 'topics.min_topic_size' must be the string 'auto' or a "
                f"positive int, got {size!r}"
            )
    return errors


def _validate_cross_field_rules(config: dict[str, Any]) -> list[str]:
    """U32-style cross-field and enum rules beyond per-key type checks."""
    errors: list[str] = []

    errors.extend(_database_block_errors(config))

    graph = config.get("graph")
    if isinstance(graph, dict):
        errors.extend(_max_hops_errors(graph.get("max_hops")))
        backend = graph.get("duckdb_graph_backend")
        if backend is not None and backend not in SUPPORTED_DUCKDB_GRAPH_BACKENDS:
            engines = ", ".join(sorted(SUPPORTED_DUCKDB_GRAPH_BACKENDS))
            errors.append(
                f"graph.duckdb_graph_backend: must be one of ({engines}), got {backend!r}"
            )

    graph_policy = config.get("graph_policy")
    if isinstance(graph_policy, dict):
        mode = graph_policy.get("mode")
        if mode not in SUPPORTED_GRAPH_POLICY_MODES:
            modes = ", ".join(sorted(SUPPORTED_GRAPH_POLICY_MODES))
            errors.append(f"graph_policy.mode: must be one of ({modes}), got {mode!r}")

    textbook = config.get("textbook")
    if isinstance(textbook, dict):
        errors.extend(_textbook_block_errors(textbook))

    queue = config.get("queue")
    if isinstance(queue, dict):
        errors.extend(_queue_block_errors(queue))

    retrieval = config.get("retrieval")
    if isinstance(retrieval, dict):
        mode = retrieval.get("mode")
        if mode not in SUPPORTED_RETRIEVAL_MODES:
            errors.append(
                f"retrieval.mode: must be one of {sorted(SUPPORTED_RETRIEVAL_MODES)}, got {mode!r}"
            )
        vector_index = retrieval.get("vector_index")
        if vector_index not in SUPPORTED_VECTOR_INDEX_MODES:
            modes = ", ".join(sorted(SUPPORTED_VECTOR_INDEX_MODES))
            errors.append(f"retrieval.vector_index: must be one of ({modes}), got {vector_index!r}")
        errors.extend(_fts_debounce_errors(retrieval.get("fts_rebuild_debounce_s")))
        if retrieval.get("matryoshka_enabled") is True:
            embedding = config.get("embedding")
            mrl = embedding.get("mrl_trained") if isinstance(embedding, dict) else None
            if mrl is not True:
                errors.append(
                    "retrieval.matryoshka_enabled: true requires embedding.mrl_trained: true "
                    "(two-stage matryoshka retrieval front-slices embeddings, which is only "
                    "sound for Matryoshka-trained models; qwen3-embedding is MRL-trained, "
                    "nomic-embed-text is not)"
                )

    storage = config.get("storage")
    if isinstance(storage, dict):
        vector_type = storage.get("vector_type")
        if vector_type not in SUPPORTED_VECTOR_TYPES:
            errors.append(
                f"storage.vector_type: must be one of {sorted(SUPPORTED_VECTOR_TYPES)}, "
                f"got {vector_type!r}"
            )

    ingest = config.get("ingest")
    if isinstance(ingest, dict):
        contextualize = ingest.get("contextualize")
        if isinstance(contextualize, dict):
            errors.extend(_contextualize_block_errors(contextualize))

    topics = config.get("topics")
    if isinstance(topics, dict):
        errors.extend(_topics_block_errors(topics))

    reranking = config.get("reranking")
    if isinstance(reranking, dict):
        errors.extend(_reranking_block_errors(reranking))
    errors.extend(_return_top_k_errors(config.get("reranking")))
    return errors


def _database_block_errors(config: dict[str, Any]) -> list[str]:
    """Enum + backend-mix rules for the storage-portability keys."""
    errors: list[str] = []
    database = config.get("database")
    if isinstance(database, dict):
        backend = database.get("backend")
        if backend is not None and backend not in SUPPORTED_DATABASE_BACKENDS:
            backends = ", ".join(sorted(SUPPORTED_DATABASE_BACKENDS))
            errors.append(f"database.backend: must be one of ({backends}), got {backend!r}")
    errors.extend(_backend_mix_errors(config))
    return errors


def _effective_extraction_backend(config: dict[str, Any]) -> str | None:
    """The extraction backend the loader's alias layer settles on.

    Mirrors corpus_kb.config.apply_extraction_backend_alias precedence: a
    non-empty ``extraction.backend`` wins; the legacy ``graph.extractor``
    alias stays authoritative only when the preferred key is absent.
    """
    extraction = config.get("extraction")
    if isinstance(extraction, dict):
        raw = extraction.get("backend")
        if isinstance(raw, str) and raw:
            return raw
    graph = config.get("graph")
    if isinstance(graph, dict):
        raw = graph.get("extractor")
        if isinstance(raw, str) and raw:
            return raw
    return None


def _backend_mix_errors(config: dict[str, Any]) -> list[str]:
    """Mirror of corpus_kb.config.backend_mix_errors (same bank texts).

    AGE and PostgresML selections cannot run while ``database.backend`` is
    duckdb; the loader refuses them AT LOAD and this validator reports the
    same combinations for the shipped YAMLs and user configs. Returns every
    violation in a fixed order; empty unless the backend is exactly
    ``duckdb`` (an unknown value is the enum rule's job, not a mix).
    """
    database = config.get("database")
    if not isinstance(database, dict) or database.get("backend") != "duckdb":
        return []
    errors: list[str] = []
    graph = config.get("graph")
    if isinstance(graph, dict) and graph.get("backend") == "age":
        errors.append(AGE_ON_DUCKDB_ERROR)
    embedding = config.get("embedding")
    if isinstance(embedding, dict) and "pgml" in (
        embedding.get("provider"),
        embedding.get("fallback_provider"),
    ):
        errors.append(PGML_EMBEDDER_ON_DUCKDB_ERROR)
    search = config.get("search")
    if isinstance(search, dict) and search.get("reranker") == "pgml":
        errors.append(PGML_RERANKER_ON_DUCKDB_ERROR)
    if _effective_extraction_backend(config) == "pgml":
        errors.append(PGML_EXTRACTION_ON_DUCKDB_ERROR)
    return errors


def _max_hops_errors(hops: object) -> list[str]:
    """Positive-int range rule for ``graph.max_hops`` (ceiling 25)."""
    if isinstance(hops, bool) or not isinstance(hops, int):
        return [f"graph.max_hops: must be an int, got {type(hops).__name__}"]
    if not MAX_HOPS_MIN <= hops <= MAX_HOPS_CEILING:
        return [
            f"graph.max_hops: must be between {MAX_HOPS_MIN} and {MAX_HOPS_CEILING}, got {hops}"
        ]
    return []


def _textbook_block_errors(textbook: dict[str, Any]) -> list[str]:
    """Enum + positive-int rules for the ``textbook`` block (Wave 1)."""
    errors: list[str] = []
    parser = textbook.get("parser")
    if parser is not None and parser not in SUPPORTED_TEXTBOOK_PARSERS:
        parsers = ", ".join(sorted(SUPPORTED_TEXTBOOK_PARSERS))
        errors.append(f"textbook.parser: must be one of ({parsers}), got {parser!r}")
    for key in ("min_pages", "window_pages"):
        value = textbook.get(key)
        if isinstance(value, bool) or not isinstance(value, int) or value < 1:
            errors.append(f"textbook.{key}: must be a positive int, got {value!r}")
    return errors


def _queue_block_errors(queue: dict[str, Any]) -> list[str]:
    """Positive-number rules for the ``queue`` worker knobs (Wave 1)."""
    errors: list[str] = []
    for key in ("poll_seconds", "stale_lease_seconds", "max_attempts"):
        value = queue.get(key)
        if isinstance(value, bool) or not isinstance(value, int) or value < 1:
            errors.append(f"queue.{key}: must be a positive int, got {value!r}")
    return errors


def _fts_debounce_errors(debounce: object) -> list[str]:
    """Positive-number rule for ``retrieval.fts_rebuild_debounce_s``."""
    if isinstance(debounce, bool) or not isinstance(debounce, (int, float)):
        return [
            f"retrieval.fts_rebuild_debounce_s: must be a positive number, "
            f"got {type(debounce).__name__}"
        ]
    if debounce <= 0:
        return [f"retrieval.fts_rebuild_debounce_s: must be a positive number, got {debounce}"]
    return []


def _reranking_block_errors(reranking: dict[str, Any]) -> list[str]:
    """Enum rule for ``reranking.backend`` (None is the shipped default)."""
    errors: list[str] = []
    backend = reranking.get("backend")
    if backend is not None and backend not in SUPPORTED_RERANK_BACKENDS:
        backends = ", ".join(sorted(SUPPORTED_RERANK_BACKENDS))
        errors.append(f"reranking.backend: must be null or one of ({backends}), got {backend!r}")
    return errors


def _return_top_k_errors(reranking: object) -> list[str]:
    """Positive-int range rule for ``reranking.return_top_k``."""
    if not isinstance(reranking, dict):
        return []
    top_k = reranking.get("return_top_k")
    if isinstance(top_k, bool) or not isinstance(top_k, int):
        return [f"reranking.return_top_k: must be an int, got {type(top_k).__name__}"]
    if not RETURN_TOP_K_MIN <= top_k <= RETURN_TOP_K_MAX:
        return [
            f"reranking.return_top_k: must be between {RETURN_TOP_K_MIN} and "
            f"{RETURN_TOP_K_MAX}, got {top_k}"
        ]
    return []


def validate_runtime_config(config: dict[str, Any]) -> list[str]:
    """Validate the runtime config.yaml keys in RUNTIME_CONFIG_KEYS.

    Every key must be PRESENT (explicit in both config.yaml files — the
    default lives in code, but the shipped YAML is the visible contract)
    and hold the expected type. Also enforces the cross-field rules:
    ``retrieval.mode`` is enum-locked to hybrid_rrf, ``storage.vector_type``
    to halfvec, ``reranking.backend`` to null or the supported set,
    ``reranking.return_top_k`` is a positive int in range, and
    ``retrieval.matryoshka_enabled: true`` requires ``embedding.mrl_trained:
    true`` (the two-stage arm front-slices embeddings, which is only sound
    for Matryoshka-trained models; the runtime refuses the same combination
    in ``config.load_config``).
    """
    errors: list[str] = []
    for dotted, expected in RUNTIME_CONFIG_KEYS.items():
        node: Any = config
        parts = dotted.split(".")
        for part in parts:
            if not isinstance(node, dict) or part not in node:
                errors.append(f"missing required key '{dotted}' (expected {expected.__name__})")
                node = None
                break
            node = node[part]
        else:
            # bool is a subclass of int: never accept a bool where a real
            # int/float is expected, and require bool-exactness for bool keys.
            # A float-expected key ALSO accepts int (web/settings.py accepts
            # int at runtime, e.g. `delay_seconds: 1`).
            if expected is bool:
                type_ok = isinstance(node, bool)
            elif isinstance(node, bool):
                type_ok = False
            else:
                type_ok = isinstance(node, expected) or (
                    expected is float and isinstance(node, int)
                )
            if not type_ok:
                errors.append(
                    f"key '{dotted}' must be {expected.__name__}, got {type(node).__name__}"
                )
    errors.extend(_validate_cross_field_rules(config))
    return errors


# ---------------------------------------------------------------------------
# File-level validation (loads from disk)
# ---------------------------------------------------------------------------


def load_json(path: Path) -> dict:
    """Load and parse a JSON file."""
    with path.open(encoding="utf-8") as f:
        return json.load(f)


def _validate_mcp_config_dir(
    project_root: Path,
) -> tuple[list[str], dict[str, Any] | None, dict[str, Any] | None, dict[str, Any] | None]:
    """Validate mcp-configs/*.json; return (errors, claude, cursor, codex configs).

    The three mcpServers configs are returned for the cross-config check.
    """
    errors: list[str] = []
    claude_config: dict | None = None
    cursor_config: dict | None = None
    codex_config: dict | None = None

    mcp_configs_dir = project_root / "mcp-configs"
    if not mcp_configs_dir.exists():
        return ["mcp-configs/ directory not found"], None, None, None

    for json_file in sorted(mcp_configs_dir.glob("*.json")):
        config = load_json(json_file)
        filename = json_file.name

        if filename == "opencode.json":
            # Should match root format
            errs = validate_opencode_config(config)
            errors.extend(f"mcp-configs/{filename}: {e}" for e in errs)
        elif filename == "claude-code.json":
            claude_config = config
            errs = validate_claude_config(config)
            errors.extend(f"mcp-configs/{filename}: {e}" for e in errs)
        elif filename == "cursor.json":
            cursor_config = config
            errs = validate_cursor_config(config)
            errors.extend(f"mcp-configs/{filename}: {e}" for e in errs)
        elif filename == "codex.json":
            # Intentional reuse: codex.json shares the mcpServers+autoApprove shape.
            codex_config = config
            errs = validate_claude_config(config)
            errors.extend(f"mcp-configs/{filename}: {e}" for e in errs)
        else:
            errors.append(f"mcp-configs/{filename}: unknown config format, skipping")
    return errors, claude_config, cursor_config, codex_config


def _validate_runtime_yaml(project_root: Path) -> list[str]:
    """Runtime config.yaml checks: root and packaged copies must agree.

    Lockstep rule for new config keys; all three validators run per copy.
    """
    errors: list[str] = []
    for relative_path in (Path("config.yaml"), Path("src") / "corpus_kb" / "config.yaml"):
        config_path = project_root / relative_path
        display = relative_path.as_posix()
        if not config_path.exists():
            errors.append(f"{display}: not found")
            continue
        runtime_cfg = load_yaml(config_path)
        errs = validate_extraction_gliner(runtime_cfg)
        errs += validate_extraction_ml(runtime_cfg)
        errs += validate_statements_block(runtime_cfg)
        errs += validate_ingestion_block(runtime_cfg)
        errs += validate_runtime_config(runtime_cfg)
        errors.extend(f"{display}: {e}" for e in errs)
    return errors


def validate_all(project_root: Path | None = None) -> list[str]:
    """Run all validations against the project's config files.

    Returns a combined list of error strings.
    """
    if project_root is None:
        project_root = Path(__file__).resolve().parent.parent.parent.parent

    errors: list[str] = []

    # 1. Validate root opencode.json (OpenCode native format)
    opencode_path = project_root / "opencode.json"
    if opencode_path.exists():
        config = load_json(opencode_path)
        errs = validate_opencode_config(config)
        errors.extend(f"opencode.json: {e}" for e in errs)
    else:
        errors.append("opencode.json not found")

    # 2. Validate mcp-configs/*.json
    mcp_errors, claude_config, cursor_config, codex_config = _validate_mcp_config_dir(project_root)
    errors.extend(mcp_errors)

    # 3. Cross-config consistency
    opencode_config = load_json(opencode_path) if opencode_path.exists() else None

    cross_errors = check_cross_config_consistency(
        opencode_config, claude_config, cursor_config, codex_config
    )
    errors.extend(f"Cross-config: {e}" for e in cross_errors)

    # 4. Check for old format backup (informational)
    bak_path = project_root / "opencode.json.bak"
    if bak_path.exists():
        bak_config = load_json(bak_path)
        bak_errors = validate_opencode_config(bak_config)
        if not bak_errors:
            errors.append(
                "opencode.json.bak: unexpectedly passes OpenCode validation (should be old format)"
            )

    # 5. Runtime config.yaml — root and packaged copies (see helper).
    errors.extend(_validate_runtime_yaml(project_root))

    return errors


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------


def main() -> None:
    """Run validation and exit with appropriate code."""
    project_root = Path(__file__).resolve().parent.parent.parent.parent
    errors = validate_all(project_root)

    if errors:
        print(f"CONFIG VALIDATION FAILED ({len(errors)} error(s)):", file=sys.stderr)
        for err in errors:
            print(f"  - {err}", file=sys.stderr)
        sys.exit(1)
    else:
        print("All config validations passed.")
        sys.exit(0)


if __name__ == "__main__":
    main()
