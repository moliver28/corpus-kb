"""Tests for corpus_kb._setup.validate_configs — active validator API.

Covers:
  1. Valid configs pass (OpenCode, Claude Code, Cursor, Codex)
  2. Old format (mcpServers) fails for OpenCode
  3. Missing autoApprove fails (OpenCode / Claude / Codex)
  4. Invalid tool name in autoApprove fails
  5. Mismatched descriptions between configs fail
  6. Missing command fails
  7. Wrong command type (string vs array) fails for OpenCode
  8. load_json helper
  9. Edge cases
  10. Real project configs pass via validate_all(project_root=ROOT)
  11. Research/notebook tool names are autoApprove-valid (todo-18 lockstep)
"""

from __future__ import annotations

import copy
import importlib.util
import json
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent

_VALIDATE_CONFIGS_PATH = ROOT / "src" / "corpus_kb" / "_setup" / "validate_configs.py"

_spec = importlib.util.spec_from_file_location("validate_configs", _VALIDATE_CONFIGS_PATH)
assert _spec is not None and _spec.loader is not None
validate_configs = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(validate_configs)

VALID_TOOL_NAMES = validate_configs.VALID_TOOL_NAMES
validate_opencode_config = validate_configs.validate_opencode_config
validate_claude_config = validate_configs.validate_claude_config
validate_cursor_config = validate_configs.validate_cursor_config
load_json = validate_configs.load_json
check_cross_config_consistency = validate_configs.check_cross_config_consistency
validate_all = validate_configs.validate_all
validate_ingestion_block = validate_configs.validate_ingestion_block
RUNTIME_CONFIG_KEYS = validate_configs.RUNTIME_CONFIG_KEYS
validate_runtime_config = validate_configs.validate_runtime_config


# ============================================================================
# Fixtures — base valid configs for each format
# ============================================================================


@pytest.fixture
def valid_opencode_config():
    """Minimal valid OpenCode native format config."""
    return {
        "$schema": "https://opencode.ai/config.json",
        "mcp": {
            "corpus-kb": {
                "type": "local",
                "description": "Local RAG system",
                "command": ["corpus-kb", "--transport", "stdio"],
                "environment": {},
                "autoApprove": ["search", "retrieve_context"],
            }
        },
    }


@pytest.fixture
def valid_claude_config():
    """Minimal valid Claude Code format config."""
    return {
        "mcpServers": {
            "corpus-kb": {
                "name": "Corpus-KB",
                "description": "Local RAG system",
                "command": "corpus-kb",
                "args": ["--transport", "stdio"],
                "env": {},
                "autoApprove": ["search", "retrieve_context"],
            }
        },
    }


@pytest.fixture
def valid_cursor_config():
    """Minimal valid Cursor format config (no autoApprove)."""
    return {
        "mcpServers": {
            "corpus-kb": {
                "name": "Corpus-KB",
                "description": "Local RAG system",
                "command": "corpus-kb",
                "args": ["--transport", "stdio"],
                "env": {},
            }
        },
    }


@pytest.fixture
def valid_codex_config():
    """Minimal valid Codex copy-template (mcpServers + autoApprove)."""
    return {
        "mcpServers": {
            "corpus-kb": {
                "name": "Corpus-KB",
                "description": "Local RAG system",
                "command": "corpus-kb",
                "args": ["--transport", "stdio"],
                "env": {},
                "autoApprove": ["search", "retrieve_context"],
            }
        },
    }


# ============================================================================
# 1. Valid configs pass
# ============================================================================


class TestValidConfigsPass:
    def test_valid_opencode_config_passes(self, valid_opencode_config):
        errors = validate_opencode_config(valid_opencode_config)
        assert errors == [], f"Expected no errors, got: {errors}"

    def test_valid_claude_config_passes(self, valid_claude_config):
        errors = validate_claude_config(valid_claude_config)
        assert errors == [], f"Expected no errors, got: {errors}"

    def test_valid_cursor_config_passes(self, valid_cursor_config):
        errors = validate_cursor_config(valid_cursor_config)
        assert errors == [], f"Expected no errors, got: {errors}"

    def test_valid_opencode_with_full_autoapprove_passes(self):
        """OpenCode config with all valid tool names should pass."""
        config = {
            "$schema": "https://opencode.ai/config.json",
            "mcp": {
                "corpus-kb": {
                    "type": "local",
                    "description": "Full tool set",
                    "command": ["corpus-kb", "--transport", "stdio"],
                    "environment": {},
                    "autoApprove": sorted(VALID_TOOL_NAMES),
                }
            },
        }
        errors = validate_opencode_config(config)
        assert errors == [], f"Expected no errors, got: {errors}"


# ============================================================================
# 2. Old format (mcpServers) fails for OpenCode
# ============================================================================


class TestOldFormatFails:
    def test_mcp_servers_key_fails_opencode(self, valid_opencode_config):
        """Using mcpServers instead of mcp should fail OpenCode validation."""
        config = copy.deepcopy(valid_opencode_config)
        config["mcpServers"] = config.pop("mcp")
        errors = validate_opencode_config(config)
        assert any("mcpServers" in e and "mcp" in e.lower() for e in errors), (
            f"Expected 'mcpServers' error, got: {errors}"
        )

    def test_missing_schema_fails_opencode(self, valid_opencode_config):
        """Missing $schema should fail OpenCode validation."""
        config = copy.deepcopy(valid_opencode_config)
        del config["$schema"]
        errors = validate_opencode_config(config)
        assert any("schema" in e.lower() for e in errors), f"Expected 'schema' error, got: {errors}"


# ============================================================================
# 3. Missing autoApprove fails (OpenCode / Claude)
# ============================================================================


class TestMissingAutoApproveFails:
    def test_missing_auto_approve_opencode_fails(self, valid_opencode_config):
        config = copy.deepcopy(valid_opencode_config)
        del config["mcp"]["corpus-kb"]["autoApprove"]
        errors = validate_opencode_config(config)
        assert any("autoApprove" in e for e in errors), (
            f"Expected 'autoApprove' error, got: {errors}"
        )

    def test_missing_auto_approve_claude_fails(self, valid_claude_config):
        config = copy.deepcopy(valid_claude_config)
        del config["mcpServers"]["corpus-kb"]["autoApprove"]
        errors = validate_claude_config(config)
        assert any("autoApprove" in e for e in errors), (
            f"Expected 'autoApprove' error, got: {errors}"
        )

    def test_cursor_allows_missing_auto_approve(self, valid_cursor_config):
        """Cursor format does NOT require autoApprove."""
        errors = validate_cursor_config(valid_cursor_config)
        assert errors == [], f"Cursor should allow missing autoApprove, got: {errors}"


# ============================================================================
# 4. Invalid tool name in autoApprove fails
# ============================================================================


class TestInvalidToolNameFails:
    def test_invalid_tool_name_opencode_fails(self, valid_opencode_config):
        config = copy.deepcopy(valid_opencode_config)
        config["mcp"]["corpus-kb"]["autoApprove"].append("nonexistent_tool_xyz")
        errors = validate_opencode_config(config)
        assert any("nonexistent_tool_xyz" in e or "invalid" in e.lower() for e in errors), (
            f"Expected invalid tool error, got: {errors}"
        )

    def test_invalid_tool_name_claude_fails(self, valid_claude_config):
        config = copy.deepcopy(valid_claude_config)
        config["mcpServers"]["corpus-kb"]["autoApprove"].append("fake_tool_123")
        errors = validate_claude_config(config)
        assert any("fake_tool_123" in e or "invalid" in e.lower() for e in errors), (
            f"Expected invalid tool error, got: {errors}"
        )


# ============================================================================
# 5. Cross-config consistency checks
# ============================================================================


class TestCrossConfigConsistency:
    def test_mismatched_descriptions_fail(self, valid_opencode_config, valid_claude_config):
        opencode = copy.deepcopy(valid_opencode_config)
        claude = copy.deepcopy(valid_claude_config)
        claude["mcpServers"]["corpus-kb"]["description"] = "Different description"
        errors = check_cross_config_consistency(opencode, claude, None)
        assert any("description" in e.lower() or "mismatch" in e.lower() for e in errors), (
            f"Expected description mismatch error, got: {errors}"
        )

    def test_matching_descriptions_pass(
        self, valid_opencode_config, valid_claude_config, valid_cursor_config
    ):
        errors = check_cross_config_consistency(
            valid_opencode_config, valid_claude_config, valid_cursor_config
        )
        desc_errors = [e for e in errors if "description" in e.lower() or "mismatch" in e.lower()]
        assert desc_errors == [], f"Matching descriptions should pass, got: {desc_errors}"

    def test_different_tool_names_fail(self, valid_opencode_config, valid_claude_config):
        opencode = copy.deepcopy(valid_opencode_config)
        claude = copy.deepcopy(valid_claude_config)
        claude["mcpServers"]["other-tool"] = {
            "name": "Other",
            "description": "Local RAG system",
            "command": "other-tool",
            "args": [],
            "env": {},
            "autoApprove": ["search"],
        }
        errors = check_cross_config_consistency(opencode, claude, None)
        assert any(
            "tool" in e.lower() or "mismatch" in e.lower() or "same set" in e.lower()
            for e in errors
        ), f"Expected tool set mismatch error, got: {errors}"


# ============================================================================
# 6. Missing command fails
# ============================================================================


class TestMissingCommandFails:
    def test_missing_command_opencode_fails(self, valid_opencode_config):
        config = copy.deepcopy(valid_opencode_config)
        del config["mcp"]["corpus-kb"]["command"]
        errors = validate_opencode_config(config)
        assert any("command" in e.lower() for e in errors), (
            f"Expected 'command' error, got: {errors}"
        )

    def test_missing_command_claude_fails(self, valid_claude_config):
        config = copy.deepcopy(valid_claude_config)
        del config["mcpServers"]["corpus-kb"]["command"]
        errors = validate_claude_config(config)
        assert any("command" in e.lower() for e in errors), (
            f"Expected 'command' error, got: {errors}"
        )

    def test_missing_command_cursor_fails(self, valid_cursor_config):
        config = copy.deepcopy(valid_cursor_config)
        del config["mcpServers"]["corpus-kb"]["command"]
        errors = validate_cursor_config(config)
        assert any("command" in e.lower() for e in errors), (
            f"Expected 'command' error, got: {errors}"
        )


# ============================================================================
# 7. Wrong command type fails (string vs array)
# ============================================================================


class TestWrongCommandTypeFails:
    def test_string_command_fails_opencode(self, valid_opencode_config):
        """OpenCode requires command as array, not string."""
        config = copy.deepcopy(valid_opencode_config)
        config["mcp"]["corpus-kb"]["command"] = "corpus-kb --transport stdio"
        errors = validate_opencode_config(config)
        assert any(
            "command" in e.lower()
            and ("array" in e.lower() or "list" in e.lower() or "type" in e.lower())
            for e in errors
        ), f"Expected command type error, got: {errors}"

    def test_array_command_fails_claude(self, valid_claude_config):
        """Claude Code requires command as string, not array."""
        config = copy.deepcopy(valid_claude_config)
        config["mcpServers"]["corpus-kb"]["command"] = [
            "corpus-kb",
            "--transport",
            "stdio",
        ]
        errors = validate_claude_config(config)
        assert any(
            "command" in e.lower() and ("string" in e.lower() or "type" in e.lower())
            for e in errors
        ), f"Expected command type error, got: {errors}"

    def test_array_command_fails_cursor(self, valid_cursor_config):
        """Cursor requires command as string, not array."""
        config = copy.deepcopy(valid_cursor_config)
        config["mcpServers"]["corpus-kb"]["command"] = [
            "corpus-kb",
            "--transport",
            "stdio",
        ]
        errors = validate_cursor_config(config)
        assert any(
            "command" in e.lower() and ("string" in e.lower() or "type" in e.lower())
            for e in errors
        ), f"Expected command type error, got: {errors}"


# ============================================================================
# 8. load_json helper
# ============================================================================


class TestLoadJson:
    def test_load_json_reads_written_config(self, valid_opencode_config):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "config.json"
            path.write_text(json.dumps(valid_opencode_config), encoding="utf-8")
            loaded = load_json(path)
        assert loaded == valid_opencode_config


# ============================================================================
# 9. Edge cases
# ============================================================================


class TestEdgeCases:
    def test_empty_config_opencode_fails(self):
        errors = validate_opencode_config({})
        assert len(errors) > 0, "Empty config should fail"

    def test_empty_config_claude_fails(self):
        errors = validate_claude_config({})
        assert len(errors) > 0, "Empty config should fail"

    def test_empty_config_cursor_fails(self):
        errors = validate_cursor_config({})
        assert len(errors) > 0, "Empty config should fail"

    def test_non_list_auto_approve_fails_opencode(self, valid_opencode_config):
        config = copy.deepcopy(valid_opencode_config)
        config["mcp"]["corpus-kb"]["autoApprove"] = "search"
        errors = validate_opencode_config(config)
        assert any(
            "autoApprove" in e and ("list" in e.lower() or "array" in e.lower()) for e in errors
        ), f"Expected autoApprove type error, got: {errors}"

    def test_non_list_auto_approve_fails_claude(self, valid_claude_config):
        config = copy.deepcopy(valid_claude_config)
        config["mcpServers"]["corpus-kb"]["autoApprove"] = "search"
        errors = validate_claude_config(config)
        assert any(
            "autoApprove" in e and ("list" in e.lower() or "array" in e.lower()) for e in errors
        ), f"Expected autoApprove type error, got: {errors}"

    def test_missing_type_fails_opencode(self, valid_opencode_config):
        config = copy.deepcopy(valid_opencode_config)
        del config["mcp"]["corpus-kb"]["type"]
        errors = validate_opencode_config(config)
        assert any("type" in e.lower() for e in errors), f"Expected 'type' error, got: {errors}"

    def test_missing_environment_fails_opencode(self, valid_opencode_config):
        config = copy.deepcopy(valid_opencode_config)
        del config["mcp"]["corpus-kb"]["environment"]
        errors = validate_opencode_config(config)
        assert any("environment" in e.lower() for e in errors), (
            f"Expected 'environment' error, got: {errors}"
        )


# ============================================================================
# 10. Integration — real project configs
# ============================================================================


def test_real_configs_pass():
    """Validate the actual opencode.json and mcp-configs/*.json files."""
    errors = validate_all(project_root=ROOT)
    assert errors == [], f"Real project config validation failed: {errors}"


# ============================================================================
# 11. Codex copy-template (todo 18) + research tool-name lockstep
# ============================================================================


class TestCodexConfig:
    def test_valid_codex_config_passes(self, valid_codex_config):
        errors = validate_claude_config(valid_codex_config)
        assert errors == [], f"Expected no errors, got: {errors}"

    def test_missing_auto_approve_fails_codex(self, valid_codex_config):
        config = copy.deepcopy(valid_codex_config)
        del config["mcpServers"]["corpus-kb"]["autoApprove"]
        errors = validate_claude_config(config)
        assert any("autoApprove" in e for e in errors)

    def test_invalid_tool_name_fails_codex(self, valid_codex_config):
        config = copy.deepcopy(valid_codex_config)
        config["mcpServers"]["corpus-kb"]["autoApprove"].append("not_a_real_tool")
        errors = validate_claude_config(config)
        assert any("not_a_real_tool" in e for e in errors)

    def test_real_codex_config_is_consistent_across_all_four(self):
        """All FOUR configs agree on tool sets + descriptions + commands."""
        opencode = load_json(ROOT / "opencode.json")
        claude = load_json(ROOT / "mcp-configs" / "claude-code.json")
        cursor = load_json(ROOT / "mcp-configs" / "cursor.json")
        codex = load_json(ROOT / "mcp-configs" / "codex.json")
        errors = check_cross_config_consistency(opencode, claude, cursor, codex)
        assert errors == [], errors

    def test_unknown_mcp_configs_file_still_flagged(self, tmp_path):
        (tmp_path / "opencode.json").write_text(
            json.dumps(
                {
                    "$schema": "https://opencode.ai/config.json",
                    "mcp": {
                        "corpus-kb": {
                            "type": "local",
                            "description": "Local RAG system",
                            "command": ["corpus-kb", "--transport", "stdio"],
                            "environment": {},
                            "autoApprove": ["search"],
                        }
                    },
                }
            ),
            encoding="utf-8",
        )
        (tmp_path / "mcp-configs").mkdir()
        (tmp_path / "mcp-configs" / "mystery.json").write_text("{}", encoding="utf-8")
        errors = validate_all(project_root=tmp_path)
        assert any("unknown config format" in e for e in errors)


def test_research_and_notebook_tools_are_valid_names():
    """r9 lockstep: research/coding surfaces (todos 11-18) autoApprove cleanly."""
    expected = {
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
    }
    assert expected <= VALID_TOOL_NAMES


# ============================================================================
# 12. Runtime ingestion block + key validation (C-Major-2)
# ============================================================================


class TestIngestionBlock:
    def test_good_block_passes(self):
        config = {"ingestion": {"strategies": {"transcript": "transcript"}, "default": "default"}}
        assert validate_ingestion_block(config) == []

    def test_missing_block_is_optional(self):
        assert validate_ingestion_block({}) == []

    def test_non_mapping_block_fails(self):
        errors = validate_ingestion_block({"ingestion": ["default"]})
        assert any("ingestion: must be a mapping" in e for e in errors)

    def test_non_mapping_strategies_fails(self):
        errors = validate_ingestion_block({"ingestion": {"strategies": ["transcript"]}})
        assert any("ingestion.strategies: must be a mapping" in e for e in errors)

    def test_empty_strategy_name_fails(self):
        errors = validate_ingestion_block(
            {"ingestion": {"strategies": {"transcript": ""}, "default": "default"}}
        )
        assert any("transcript" in e and "non-empty" in e for e in errors)

    def test_non_string_strategy_name_fails(self):
        errors = validate_ingestion_block(
            {"ingestion": {"strategies": {"transcript": 3}, "default": "default"}}
        )
        assert any("non-empty" in e and "strategy-name" in e for e in errors)

    def test_missing_default_fails(self):
        errors = validate_ingestion_block({"ingestion": {"strategies": {}}})
        assert any("ingestion.default: must be a non-empty string" in e for e in errors)

    def test_empty_default_fails(self):
        errors = validate_ingestion_block({"ingestion": {"strategies": {}, "default": ""}})
        assert any("ingestion.default: must be a non-empty string" in e for e in errors)

    def test_real_project_ingestion_block_passes(self):
        for name in ("config.yaml", "src/corpus_kb/config.yaml"):
            cfg = validate_configs.load_yaml(ROOT / name)
            assert validate_ingestion_block(cfg) == [], name


class TestRuntimeIngestionKeys:
    """RUNTIME_CONFIG_KEYS learned the ingestion.* keys (presence + type)."""

    def test_ingestion_keys_declared(self):
        assert RUNTIME_CONFIG_KEYS["ingestion.default"] is str
        assert RUNTIME_CONFIG_KEYS["ingestion.strategies"] is dict

    def test_shipped_yamls_pass_runtime_validation(self):
        for name in ("config.yaml", "src/corpus_kb/config.yaml"):
            cfg = validate_configs.load_yaml(ROOT / name)
            assert validate_runtime_config(cfg) == [], name

    def test_missing_ingestion_default_fails(self):
        config = {
            "search": {"exclude_degraded": True, "qa_context_in_fts": False},
            "ingestion": {"strategies": {}},
        }
        errors = validate_runtime_config(config)
        assert any("missing required key 'ingestion.default'" in e for e in errors)

    def test_mistyped_ingestion_strategies_fails(self):
        config = {
            "search": {"exclude_degraded": True, "qa_context_in_fts": False},
            "ingestion": {"strategies": ["transcript"], "default": "default"},
        }
        errors = validate_runtime_config(config)
        assert any("key 'ingestion.strategies' must be dict" in e for e in errors)


class TestRuntimeWebKeys:
    """RUNTIME_CONFIG_KEYS learned the web.* keys (presence + type)."""

    def test_web_keys_declared(self):
        assert RUNTIME_CONFIG_KEYS["web.user_agent"] is str
        assert RUNTIME_CONFIG_KEYS["web.timeout_seconds"] is int
        assert RUNTIME_CONFIG_KEYS["web.max_bytes"] is int
        assert RUNTIME_CONFIG_KEYS["web.respect_robots"] is bool
        assert RUNTIME_CONFIG_KEYS["web.max_pages"] is int
        assert RUNTIME_CONFIG_KEYS["web.max_depth"] is int
        assert RUNTIME_CONFIG_KEYS["web.delay_seconds"] is float
        assert RUNTIME_CONFIG_KEYS["web.allow_private_addresses"] is bool

    def test_shipped_yamls_carry_web_section(self):
        expected = {
            "user_agent",
            "timeout_seconds",
            "max_bytes",
            "respect_robots",
            "max_pages",
            "max_depth",
            "delay_seconds",
            "allow_private_addresses",
        }
        for name in ("config.yaml", "src/corpus_kb/config.yaml"):
            cfg = validate_configs.load_yaml(ROOT / name)
            assert validate_runtime_config(cfg) == [], name
            web = cfg["web"]
            assert isinstance(web, dict) and expected <= set(web), name

    def test_missing_and_mistyped_web_keys_fail(self):
        errors = validate_runtime_config({})
        assert any("missing required key 'web.user_agent'" in e for e in errors)
        mistyped = {
            "web": {"user_agent": 42, "timeout_seconds": "30", "delay_seconds": True},
        }
        errors = validate_runtime_config(mistyped)
        assert any("key 'web.user_agent' must be str" in e for e in errors)
        assert any("key 'web.timeout_seconds' must be int" in e for e in errors)
        assert any("key 'web.delay_seconds' must be float" in e for e in errors)

    def test_float_keys_accept_int_but_not_bool(self):
        # web/settings.py accepts int where a float is declared (e.g.
        # `delay_seconds: 1`), so the validator must not reject it; a bool
        # stays rejected for every non-bool key. Base: the shipped YAML
        # (the full canonical key set), then override the one key under test.
        config = validate_configs.load_yaml(ROOT / "src/corpus_kb/config.yaml")
        config["web"]["delay_seconds"] = 1
        assert validate_runtime_config(config) == []
        config["web"]["delay_seconds"] = True
        errors = validate_runtime_config(config)
        assert any("key 'web.delay_seconds' must be float" in e for e in errors)


class TestRuntimeCanonicalKeys:
    """RUNTIME_CONFIG_KEYS learned the config-v2 canonical namespaces."""

    def test_canonical_keys_declared(self):
        expected = {
            "embedding.query_instruction": bool,
            "storage.vector_type": str,
            "ingest.chunking.enabled": bool,
            "ingest.chunking.child_tokens": int,
            "ingest.chunking.chunk_tokens": int,
            "ingest.chunking.parent_tokens": int,
            "ingest.chunking.overlap_tokens": int,
            "ingest.chunking.respect_boundaries": list,
            "ingest.contextualize.enabled": bool,
            "retrieval.mode": str,
            "retrieval.vector_candidates": int,
            "retrieval.lexical_candidates": int,
            "retrieval.rrf_k": int,
            "retrieval.expand_to_parent": bool,
            "retrieval.hnsw.m": int,
            "retrieval.hnsw.ef_construction": int,
            "reranking.enabled": bool,
            "reranking.return_top_k": int,
        }
        for dotted, expected_type in expected.items():
            assert RUNTIME_CONFIG_KEYS[dotted] is expected_type, dotted

    def test_legacy_search_keys_are_gone_from_the_contract(self):
        # The shipped YAMLs carry only canonical keys; the legacy names are
        # aliases the LOADER accepts, not keys the SHIPPED YAML must have.
        assert "search.exclude_degraded" not in RUNTIME_CONFIG_KEYS
        assert "search.qa_context_in_fts" not in RUNTIME_CONFIG_KEYS
        assert "search.matryoshka_enabled" not in RUNTIME_CONFIG_KEYS

    def test_mode_enum_locked_to_hybrid_rrf(self):
        config = validate_configs.load_yaml(ROOT / "src/corpus_kb/config.yaml")
        assert validate_runtime_config(config) == []
        config["retrieval"]["mode"] = "vector_only"
        errors = validate_runtime_config(config)
        assert any("retrieval.mode" in e and "hybrid_rrf" in e for e in errors)

    def test_vector_type_enum_locked_to_halfvec(self):
        config = validate_configs.load_yaml(ROOT / "src/corpus_kb/config.yaml")
        config["storage"]["vector_type"] = "vector"
        errors = validate_runtime_config(config)
        assert any("storage.vector_type" in e and "halfvec" in e for e in errors)

    def test_contextualize_backend_must_be_ollama_when_present(self):
        config = validate_configs.load_yaml(ROOT / "src/corpus_kb/config.yaml")
        config["ingest"]["contextualize"]["backend"] = "openai"
        errors = validate_runtime_config(config)
        assert any("ingest.contextualize.backend" in e for e in errors)

    def test_return_top_k_range_enforced(self):
        # The exact wording is the contract: rerank_rules.py is the one
        # rules source now, so these messages must not drift again.
        config = validate_configs.load_yaml(ROOT / "src/corpus_kb/config.yaml")
        config["reranking"]["return_top_k"] = 0
        errors = validate_runtime_config(config)
        assert "reranking.return_top_k must be between 1 and 1000; got 0" in errors
        config["reranking"]["return_top_k"] = 1001
        errors = validate_runtime_config(config)
        assert "reranking.return_top_k must be between 1 and 1000; got 1001" in errors
        config["reranking"]["return_top_k"] = "20"
        errors = validate_runtime_config(config)
        assert "reranking.return_top_k must be an int; got '20'" in errors

    def test_matryoshka_cross_field_rule_on_retrieval(self):
        config = validate_configs.load_yaml(ROOT / "src/corpus_kb/config.yaml")
        config["retrieval"]["matryoshka_enabled"] = True
        config["embedding"]["mrl_trained"] = False
        errors = validate_runtime_config(config)
        assert any(
            "retrieval.matryoshka_enabled" in e and "embedding.mrl_trained" in e for e in errors
        )


class TestRuntimeRerankerBackendKeys:
    """RUNTIME_CONFIG_KEYS learned reranking.llamacpp_url; backend is enum-checked."""

    def test_llamacpp_url_key_declared(self):
        assert RUNTIME_CONFIG_KEYS["reranking.llamacpp_url"] is str

    def test_backend_not_a_single_type_key(self):
        # null is the shipped default, so the null-or-enum union is
        # enforced by _reranking_block_errors, not the single-type map.
        assert "reranking.backend" not in RUNTIME_CONFIG_KEYS
        assert "llamacpp" in validate_configs.SUPPORTED_RERANK_BACKENDS

    def test_shipped_yamls_carry_llamacpp_url_and_validate(self):
        for name in ("config.yaml", "src/corpus_kb/config.yaml"):
            cfg = validate_configs.load_yaml(ROOT / name)
            assert validate_runtime_config(cfg) == [], name
            assert cfg["reranking"]["llamacpp_url"] == "http://localhost:8080", name

    def test_backend_enum_accepts_shipped_and_llamacpp_values(self):
        config = validate_configs.load_yaml(ROOT / "src/corpus_kb/config.yaml")
        for value in (None, "generative", "fake", "llamacpp"):
            config["reranking"]["backend"] = value
            errors = validate_runtime_config(config)
            assert not any("reranking.backend" in e for e in errors), value

    def test_unknown_backend_value_rejected(self):
        config = validate_configs.load_yaml(ROOT / "src/corpus_kb/config.yaml")
        config["reranking"]["backend"] = "ollama"
        errors = validate_runtime_config(config)
        assert any("reranking.backend" in e and "llamacpp" in e for e in errors)

    def test_mistyped_llamacpp_url_rejected(self):
        config = validate_configs.load_yaml(ROOT / "src/corpus_kb/config.yaml")
        config["reranking"]["llamacpp_url"] = 8080
        errors = validate_runtime_config(config)
        assert any("key 'reranking.llamacpp_url' must be str" in e for e in errors)


class TestRuntimeTextbookKeys:
    """RUNTIME_CONFIG_KEYS learned the textbook isolation + cache-cap keys."""

    def test_textbook_keys_declared(self):
        assert RUNTIME_CONFIG_KEYS["textbook.window_subprocess"] is bool
        assert RUNTIME_CONFIG_KEYS["textbook.window_timeout_s"] is float
        assert RUNTIME_CONFIG_KEYS["textbook.cache_max_mb"] is int
        assert RUNTIME_CONFIG_KEYS["textbook.structured_output"] is bool
        assert RUNTIME_CONFIG_KEYS["textbook.citations"] is bool

    def test_shipped_yamls_carry_the_new_textbook_keys(self):
        for name in ("config.yaml", "src/corpus_kb/config.yaml"):
            cfg = validate_configs.load_yaml(ROOT / name)
            assert validate_runtime_config(cfg) == [], name
            assert cfg["textbook"]["window_subprocess"] is True, name
            assert cfg["textbook"]["window_timeout_s"] == 3600.0, name
            assert cfg["textbook"]["cache_max_mb"] == 2048, name
            assert cfg["textbook"]["structured_output"] is False, name
            assert cfg["textbook"]["citations"] is False, name

    def test_extended_textbook_block_passes(self):
        errors = validate_configs._textbook_block_errors(
            {
                "parser": "docling",
                "min_pages": 120,
                "window_pages": 50,
                "window_subprocess": True,
                "window_timeout_s": 3600.0,
                "cache_max_mb": 2048,
            }
        )
        assert errors == []

    def test_bad_new_textbook_values_rejected(self):
        errors = validate_configs._textbook_block_errors(
            {
                "window_subprocess": "yes",
                "window_timeout_s": -1,
                "cache_max_mb": "big",
            }
        )
        assert any("textbook.window_subprocess" in e for e in errors)
        assert any("textbook.window_timeout_s" in e for e in errors)
        assert any("textbook.cache_max_mb" in e for e in errors)

    def test_cache_cap_zero_is_valid(self):
        block = {"min_pages": 120, "window_pages": 50, "cache_max_mb": 0}
        errors = validate_configs._textbook_block_errors(block)
        assert errors == []

    def test_structured_output_flag_rules(self):
        block = {"min_pages": 120, "window_pages": 50}
        ok = validate_configs._textbook_block_errors({**block, "structured_output": True})
        assert ok == []
        bad = validate_configs._textbook_block_errors({**block, "structured_output": "yes"})
        assert any("textbook.structured_output: must be a bool" in e for e in bad)

    def test_mistyped_structured_output_rejected_by_type_map(self):
        config = validate_configs.load_yaml(ROOT / "src/corpus_kb/config.yaml")
        config["textbook"]["structured_output"] = "yes"
        errors = validate_runtime_config(config)
        assert any("key 'textbook.structured_output' must be bool" in e for e in errors)

    def test_citations_flag_rules(self):
        block = {"min_pages": 120, "window_pages": 50}
        ok = validate_configs._textbook_block_errors({**block, "citations": True})
        assert ok == []
        bad = validate_configs._textbook_block_errors({**block, "citations": "yes"})
        assert any("textbook.citations: must be a bool" in e for e in bad)

    def test_mistyped_citations_rejected_by_type_map(self):
        config = validate_configs.load_yaml(ROOT / "src/corpus_kb/config.yaml")
        config["textbook"]["citations"] = "yes"
        errors = validate_runtime_config(config)
        assert any("key 'textbook.citations' must be bool" in e for e in errors)
    def test_parser_enum_admits_paddle_vl(self):
        ok = validate_configs._textbook_block_errors(
            {
                "parser": "paddle-vl",
                "min_pages": 120,
                "window_pages": 50,
                "window_subprocess": True,
                "window_timeout_s": 3600.0,
                "cache_max_mb": 2048,
            }
        )
        assert ok == []
        assert "paddle-vl" in validate_configs.SUPPORTED_TEXTBOOK_PARSERS

    def test_parser_enum_rejects_unknown_names(self):
        bad = validate_configs._textbook_block_errors({"parser": "tesseract", "min_pages": 120})
        assert any("textbook.parser: must be one of" in e and "paddle-vl" in e for e in bad)

    def test_mistyped_window_timeout_rejected_by_type_map(self):
        config = validate_configs.load_yaml(ROOT / "src/corpus_kb/config.yaml")
        config["textbook"]["window_timeout_s"] = "long"
        errors = validate_runtime_config(config)
        assert any("key 'textbook.window_timeout_s' must be float" in e for e in errors)
