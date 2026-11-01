"""Config validation script for OpenCode CI enforcement.

Validates MCP configuration files across four editor formats:
  - OpenCode native (mcp key, command as array)
  - Claude Code (mcpServers key, command as string + args)
  - Cursor (mcpServers key, command as string + args, no autoApprove)
  - Codex (mcpServers key + autoApprove — copy-template for Codex CLI users)

Usage:
    poe validate-configs   (python src/corpus_kb/_setup/validate_configs.py)

Exit code 0 = all valid, 1 = validation errors found.

Zero-drift split (issue #231): the four concerns moved to sibling
modules; THIS entry module re-exports every historical
``validate_configs.<name>`` symbol so tests and the CLI keep working
byte-for-byte:

  validate_configs_toolnames.py    autoApprove allowlist + shared list rule
  validate_configs_mcp.py          per-editor MCP client validators
  validate_configs_consistency.py  cross-config tool/description checks
  validate_configs_blocks.py       extraction/statements/ingestion blocks + YAML loader
  validate_configs_keys.py         runtime key map + enum/rule constants
  validate_configs_rules.py        per-block rule helpers
  validate_configs_runtime.py      key type walk + cross-field rules
  validate_configs_cli.py          file-level runs + CLI entry point
"""

from __future__ import annotations

try:
    # Package context (installed checkout, tests).
    from corpus_kb._setup.validate_configs_blocks import (
        GLINER_MENTION_TYPES,
        load_yaml,
        validate_extraction_gliner,
        validate_extraction_ml,
        validate_ingestion_block,
        validate_statements_block,
    )
    from corpus_kb._setup.validate_configs_cli import (
        _validate_mcp_config_dir,
        _validate_runtime_yaml,
        load_json,
        main,
        validate_all,
    )
    from corpus_kb._setup.validate_configs_consistency import (
        _description_mismatches,
        _extract_tool_set,
        _tool_set_mismatches,
        check_cross_config_consistency,
    )
    from corpus_kb._setup.validate_configs_keys import (
        AGE_ON_DUCKDB_ERROR,
        MAX_HOPS_CEILING,
        MAX_HOPS_MIN,
        PGML_EMBEDDER_ON_DUCKDB_ERROR,
        PGML_EXTRACTION_ON_DUCKDB_ERROR,
        PGML_RERANKER_ON_DUCKDB_ERROR,
        RUNTIME_CONFIG_KEYS,
        SUPPORTED_APPLY_TO_TARGETS,
        SUPPORTED_CONTEXTUAL_PROFILES,
        SUPPORTED_DATABASE_BACKENDS,
        SUPPORTED_DUCKDB_GRAPH_BACKENDS,
        SUPPORTED_GRAPH_POLICY_MODES,
        SUPPORTED_RERANK_BACKENDS,
        SUPPORTED_RETRIEVAL_MODES,
        SUPPORTED_TEXTBOOK_PARSERS,
        SUPPORTED_TOPIC_METHODS,
        SUPPORTED_VECTOR_INDEX_MODES,
        SUPPORTED_VECTOR_TYPES,
        backend_error,
        return_top_k_error,
    )
    from corpus_kb._setup.validate_configs_mcp import (
        _validate_claude_tool,
        _validate_cursor_tool,
        _validate_opencode_tool,
        validate_claude_config,
        validate_cursor_config,
        validate_opencode_config,
    )
    from corpus_kb._setup.validate_configs_rules import (
        _apply_to_errors,
        _backend_mix_errors,
        _contextualize_block_errors,
        _database_block_errors,
        _effective_extraction_backend,
        _fts_debounce_errors,
        _max_hops_errors,
        _queue_block_errors,
        _reranking_block_errors,
        _return_top_k_errors,
        _textbook_block_errors,
        _topics_block_errors,
    )
    from corpus_kb._setup.validate_configs_runtime import (
        _validate_cross_field_rules,
        validate_runtime_config,
    )
    from corpus_kb._setup.validate_configs_toolnames import (
        VALID_TOOL_NAMES,
        _auto_approve_errors,
    )
except ImportError:  # pragma: no cover - script context (CI validate job)
    # Script context: the CI validate-configs job installs only PyYAML, so
    # corpus_kb is NOT importable. sys.path[0] is this directory; load the
    # siblings as top-level modules.
    import importlib as _importlib

    _modules = {
        name: _importlib.import_module(name)
        for name in (
            "validate_configs_blocks",
            "validate_configs_cli",
            "validate_configs_consistency",
            "validate_configs_keys",
            "validate_configs_mcp",
            "validate_configs_rules",
            "validate_configs_runtime",
            "validate_configs_toolnames",
        )
    }
    _blocks = _modules["validate_configs_blocks"]
    GLINER_MENTION_TYPES = _blocks.GLINER_MENTION_TYPES
    load_yaml = _blocks.load_yaml
    validate_extraction_gliner = _blocks.validate_extraction_gliner
    validate_extraction_ml = _blocks.validate_extraction_ml
    validate_ingestion_block = _blocks.validate_ingestion_block
    validate_statements_block = _blocks.validate_statements_block
    _cli = _modules["validate_configs_cli"]
    _validate_mcp_config_dir = _cli._validate_mcp_config_dir
    _validate_runtime_yaml = _cli._validate_runtime_yaml
    load_json = _cli.load_json
    main = _cli.main
    validate_all = _cli.validate_all
    _consistency = _modules["validate_configs_consistency"]
    _description_mismatches = _consistency._description_mismatches
    _extract_tool_set = _consistency._extract_tool_set
    _tool_set_mismatches = _consistency._tool_set_mismatches
    check_cross_config_consistency = _consistency.check_cross_config_consistency
    _keys = _modules["validate_configs_keys"]
    AGE_ON_DUCKDB_ERROR = _keys.AGE_ON_DUCKDB_ERROR
    MAX_HOPS_CEILING = _keys.MAX_HOPS_CEILING
    MAX_HOPS_MIN = _keys.MAX_HOPS_MIN
    PGML_EMBEDDER_ON_DUCKDB_ERROR = _keys.PGML_EMBEDDER_ON_DUCKDB_ERROR
    PGML_EXTRACTION_ON_DUCKDB_ERROR = _keys.PGML_EXTRACTION_ON_DUCKDB_ERROR
    PGML_RERANKER_ON_DUCKDB_ERROR = _keys.PGML_RERANKER_ON_DUCKDB_ERROR
    RUNTIME_CONFIG_KEYS = _keys.RUNTIME_CONFIG_KEYS
    SUPPORTED_APPLY_TO_TARGETS = _keys.SUPPORTED_APPLY_TO_TARGETS
    SUPPORTED_CONTEXTUAL_PROFILES = _keys.SUPPORTED_CONTEXTUAL_PROFILES
    SUPPORTED_DATABASE_BACKENDS = _keys.SUPPORTED_DATABASE_BACKENDS
    SUPPORTED_DUCKDB_GRAPH_BACKENDS = _keys.SUPPORTED_DUCKDB_GRAPH_BACKENDS
    SUPPORTED_GRAPH_POLICY_MODES = _keys.SUPPORTED_GRAPH_POLICY_MODES
    SUPPORTED_RERANK_BACKENDS = _keys.SUPPORTED_RERANK_BACKENDS
    SUPPORTED_RETRIEVAL_MODES = _keys.SUPPORTED_RETRIEVAL_MODES
    SUPPORTED_TEXTBOOK_PARSERS = _keys.SUPPORTED_TEXTBOOK_PARSERS
    SUPPORTED_TOPIC_METHODS = _keys.SUPPORTED_TOPIC_METHODS
    SUPPORTED_VECTOR_INDEX_MODES = _keys.SUPPORTED_VECTOR_INDEX_MODES
    SUPPORTED_VECTOR_TYPES = _keys.SUPPORTED_VECTOR_TYPES
    backend_error = _keys.backend_error
    return_top_k_error = _keys.return_top_k_error
    _mcp = _modules["validate_configs_mcp"]
    _validate_claude_tool = _mcp._validate_claude_tool
    _validate_cursor_tool = _mcp._validate_cursor_tool
    _validate_opencode_tool = _mcp._validate_opencode_tool
    validate_claude_config = _mcp.validate_claude_config
    validate_cursor_config = _mcp.validate_cursor_config
    validate_opencode_config = _mcp.validate_opencode_config
    _rules = _modules["validate_configs_rules"]
    _apply_to_errors = _rules._apply_to_errors
    _backend_mix_errors = _rules._backend_mix_errors
    _contextualize_block_errors = _rules._contextualize_block_errors
    _database_block_errors = _rules._database_block_errors
    _effective_extraction_backend = _rules._effective_extraction_backend
    _fts_debounce_errors = _rules._fts_debounce_errors
    _max_hops_errors = _rules._max_hops_errors
    _queue_block_errors = _rules._queue_block_errors
    _reranking_block_errors = _rules._reranking_block_errors
    _return_top_k_errors = _rules._return_top_k_errors
    _textbook_block_errors = _rules._textbook_block_errors
    _topics_block_errors = _rules._topics_block_errors
    _runtime = _modules["validate_configs_runtime"]
    _validate_cross_field_rules = _runtime._validate_cross_field_rules
    validate_runtime_config = _runtime.validate_runtime_config
    _toolnames = _modules["validate_configs_toolnames"]
    VALID_TOOL_NAMES = _toolnames.VALID_TOOL_NAMES
    _auto_approve_errors = _toolnames._auto_approve_errors

__all__ = [
    "AGE_ON_DUCKDB_ERROR",
    "GLINER_MENTION_TYPES",
    "MAX_HOPS_CEILING",
    "MAX_HOPS_MIN",
    "PGML_EMBEDDER_ON_DUCKDB_ERROR",
    "PGML_EXTRACTION_ON_DUCKDB_ERROR",
    "PGML_RERANKER_ON_DUCKDB_ERROR",
    "RUNTIME_CONFIG_KEYS",
    "SUPPORTED_APPLY_TO_TARGETS",
    "SUPPORTED_CONTEXTUAL_PROFILES",
    "SUPPORTED_DATABASE_BACKENDS",
    "SUPPORTED_DUCKDB_GRAPH_BACKENDS",
    "SUPPORTED_GRAPH_POLICY_MODES",
    "SUPPORTED_RERANK_BACKENDS",
    "SUPPORTED_RETRIEVAL_MODES",
    "SUPPORTED_TEXTBOOK_PARSERS",
    "SUPPORTED_TOPIC_METHODS",
    "SUPPORTED_VECTOR_INDEX_MODES",
    "SUPPORTED_VECTOR_TYPES",
    "VALID_TOOL_NAMES",
    "backend_error",
    "check_cross_config_consistency",
    "load_json",
    "load_yaml",
    "main",
    "return_top_k_error",
    "validate_all",
    "validate_claude_config",
    "validate_cursor_config",
    "validate_extraction_gliner",
    "validate_extraction_ml",
    "validate_ingestion_block",
    "validate_opencode_config",
    "validate_runtime_config",
    "validate_statements_block",
]

if __name__ == "__main__":
    main()
