"""Tests for corpus_kb._setup.validate_configs — active validator API.

Covers:
  1. Valid configs pass (OpenCode, Claude Code, Cursor)
  2. Old format (mcpServers) fails for OpenCode
  3. Missing autoApprove fails (OpenCode / Claude)
  4. Invalid tool name in autoApprove fails
  5. Mismatched descriptions between configs fail
  6. Missing command fails
  7. Wrong command type (string vs array) fails for OpenCode
  8. load_json helper
  9. Real project configs pass via validate_all(project_root=ROOT)
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
