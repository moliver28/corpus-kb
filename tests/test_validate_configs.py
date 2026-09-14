"""Tests for scripts/validate_configs.py — config format validation.

Covers:
  1. Valid configs pass (OpenCode, Claude Code, Cursor)
  2. Old format (mcpServers) fails for OpenCode
  3. Missing autoApprove fails (OpenCode / Claude)
  4. Invalid tool name in autoApprove fails
  5. Mismatched descriptions between configs fail
  6. Missing command fails
  7. Wrong command type (string vs array) fails for OpenCode
"""

from __future__ import annotations

import copy
import json
import tempfile
from pathlib import Path

import pytest

# Import the validation functions from the script
import sys
sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))
from validate_configs import (
    VALID_TOOL_NAMES,
    validate_opencode_format,
    validate_claude_code_format,
    validate_cursor_format,
    validate_old_format,
    cross_config_consistency,
    errors as global_errors,
    error as global_error,
    ValidationError,
)


def _clear_errors():
    """Clear the global errors list before each test."""
    global_errors.clear()


def _run_validator(validator_func, config, filename="test.json"):
    """Run a validator that uses global error accumulation and return errors."""
    _clear_errors()
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_path = Path(tmpdir) / filename
        with open(tmp_path, "w") as f:
            json.dump(config, f)
        validator_func(tmp_path, config)
    return [str(e) for e in global_errors]


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
        errors = _run_validator(validate_opencode_format, valid_opencode_config)
        assert errors == [], f"Expected no errors, got: {errors}"

    def test_valid_claude_config_passes(self, valid_claude_config):
        errors = _run_validator(validate_claude_code_format, valid_claude_config, "claude-code.json")
        assert errors == [], f"Expected no errors, got: {errors}"

    def test_valid_cursor_config_passes(self, valid_cursor_config):
        errors = _run_validator(validate_cursor_format, valid_cursor_config, "cursor.json")
        assert errors == [], f"Expected no errors, got: {errors}"

    def test_valid_opencode_with_full_autoapprove(self):
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
        errors = _run_validator(validate_opencode_format, config)
        assert errors == []


# ============================================================================
# 2. Old format (mcpServers) fails for OpenCode
# ============================================================================

class TestOldFormatFails:
    def test_mcpServers_key_fails_opencode(self, valid_opencode_config):
        """Using mcpServers instead of mcp should fail OpenCode validation."""
        config = copy.deepcopy(valid_opencode_config)
        config["mcpServers"] = config.pop("mcp")
        errors = _run_validator(validate_opencode_format, config)
        assert any("mcpServers" in e and "mcp" in e.lower() for e in errors), f"Expected 'mcpServers' error, got: {errors}"

    def test_missing_schema_fails_opencode(self, valid_opencode_config):
        """Missing $schema should fail OpenCode validation."""
        config = copy.deepcopy(valid_opencode_config)
        del config["$schema"]
        errors = _run_validator(validate_opencode_format, config)
        assert any("schema" in e.lower() for e in errors), f"Expected 'schema' error, got: {errors}"


# ============================================================================
# 3. Missing autoApprove fails (OpenCode / Claude)
# ============================================================================

class TestMissingAutoApproveFails:
    def test_missing_autoApprove_opencode_fails(self, valid_opencode_config):
        config = copy.deepcopy(valid_opencode_config)
        del config["mcp"]["corpus-kb"]["autoApprove"]
        errors = _run_validator(validate_opencode_format, config)
        assert any("autoApprove" in e for e in errors), f"Expected 'autoApprove' error, got: {errors}"

    def test_missing_autoApprove_claude_fails(self, valid_claude_config):
        config = copy.deepcopy(valid_claude_config)
        del config["mcpServers"]["corpus-kb"]["autoApprove"]
        errors = _run_validator(validate_claude_code_format, config, "claude-code.json")
        assert any("autoApprove" in e for e in errors), f"Expected 'autoApprove' error, got: {errors}"

    def test_cursor_allows_missing_autoApprove(self, valid_cursor_config):
        """Cursor format does NOT require autoApprove."""
        errors = _run_validator(validate_cursor_format, valid_cursor_config, "cursor.json")
        assert errors == [], f"Cursor should allow missing autoApprove, got: {errors}"


# ============================================================================
# 4. Invalid tool name in autoApprove fails
# ============================================================================

class TestInvalidToolNameFails:
    def test_invalid_tool_name_opencode_fails(self, valid_opencode_config):
        config = copy.deepcopy(valid_opencode_config)
        config["mcp"]["corpus-kb"]["autoApprove"].append("nonexistent_tool_xyz")
        errors = _run_validator(validate_opencode_format, config)
        assert any("nonexistent_tool_xyz" in e or "invalid" in e.lower() for e in errors), \
            f"Expected invalid tool error, got: {errors}"

    def test_invalid_tool_name_claude_fails(self, valid_claude_config):
        config = copy.deepcopy(valid_claude_config)
        config["mcpServers"]["corpus-kb"]["autoApprove"].append("fake_tool_123")
        errors = _run_validator(validate_claude_code_format, config, "claude-code.json")
        assert any("fake_tool_123" in e or "invalid" in e.lower() for e in errors), \
            f"Expected invalid tool error, got: {errors}"


# ============================================================================
# 5. Mismatched descriptions between configs fail
# ============================================================================

class TestMismatchedDescriptionsFail:
    def test_mismatched_descriptions_fail(self, valid_opencode_config, valid_claude_config):
        opencode = copy.deepcopy(valid_opencode_config)
        claude = copy.deepcopy(valid_claude_config)
        claude["mcpServers"]["corpus-kb"]["description"] = "Completely different description"
        _clear_errors()
        configs = {"opencode.json": opencode, "claude-code.json": claude}
        cross_config_consistency(configs)
        errors = [str(e) for e in global_errors]
        assert any("description" in e.lower() or "mismatch" in e.lower() for e in errors), \
            f"Expected description mismatch error, got: {errors}"

    def test_matching_descriptions_pass(self, valid_opencode_config, valid_claude_config, valid_cursor_config):
        _clear_errors()
        configs = {
            "opencode.json": valid_opencode_config,
            "claude-code.json": valid_claude_config,
            "cursor.json": valid_cursor_config,
        }
        cross_config_consistency(configs)
        errors = [str(e) for e in global_errors]
        desc_errors = [e for e in errors if "description" in e.lower() or "mismatch" in e.lower()]
        assert desc_errors == [], f"Matching descriptions should pass, got: {desc_errors}"


# ============================================================================
# 6. Missing command fails
# ============================================================================

class TestMissingCommandFails:
    def test_missing_command_opencode_fails(self, valid_opencode_config):
        config = copy.deepcopy(valid_opencode_config)
        del config["mcp"]["corpus-kb"]["command"]
        errors = _run_validator(validate_opencode_format, config)
        assert any("command" in e.lower() for e in errors), f"Expected 'command' error, got: {errors}"

    def test_missing_command_claude_fails(self, valid_claude_config):
        config = copy.deepcopy(valid_claude_config)
        del config["mcpServers"]["corpus-kb"]["command"]
        errors = _run_validator(validate_claude_code_format, config, "claude-code.json")
        assert any("command" in e.lower() for e in errors), f"Expected 'command' error, got: {errors}"

    def test_missing_command_cursor_fails(self, valid_cursor_config):
        config = copy.deepcopy(valid_cursor_config)
        del config["mcpServers"]["corpus-kb"]["command"]
        errors = _run_validator(validate_cursor_format, config, "cursor.json")
        assert any("command" in e.lower() for e in errors), f"Expected 'command' error, got: {errors}"


# ============================================================================
# 7. Wrong command type fails (string vs array)
# ============================================================================

class TestWrongCommandTypeFails:
    def test_string_command_fails_opencode(self, valid_opencode_config):
        """OpenCode requires command as array, not string."""
        config = copy.deepcopy(valid_opencode_config)
        config["mcp"]["corpus-kb"]["command"] = "corpus-kb --transport stdio"
        errors = _run_validator(validate_opencode_format, config)
        assert any("command" in e.lower() and ("array" in e.lower() or "list" in e.lower() or "type" in e.lower()) for e in errors), \
            f"Expected command type error, got: {errors}"

    def test_array_command_fails_claude(self, valid_claude_config):
        """Claude Code requires command as string, not array."""
        config = copy.deepcopy(valid_claude_config)
        config["mcpServers"]["corpus-kb"]["command"] = ["corpus-kb", "--transport", "stdio"]
        errors = _run_validator(validate_claude_code_format, config, "claude-code.json")
        assert any("command" in e.lower() and ("string" in e.lower() or "type" in e.lower()) for e in errors), \
            f"Expected command type error, got: {errors}"

    def test_array_command_fails_cursor(self, valid_cursor_config):
        """Cursor requires command as string, not array."""
        config = copy.deepcopy(valid_cursor_config)
        config["mcpServers"]["corpus-kb"]["command"] = ["corpus-kb", "--transport", "stdio"]
        errors = _run_validator(validate_cursor_format, config, "cursor.json")
        assert any("command" in e.lower() and ("string" in e.lower() or "type" in e.lower()) for e in errors), \
            f"Expected command type error, got: {errors}"


# ============================================================================
# 8. Cross-config consistency — tool set mismatch
# ============================================================================

class TestCrossConfigToolSetMismatch:
    def test_different_tool_names_fail(self, valid_opencode_config, valid_claude_config):
        opencode = copy.deepcopy(valid_opencode_config)
        claude = copy.deepcopy(valid_claude_config)
        # Add a different tool name in Claude config
        claude["mcpServers"]["other-tool"] = {
            "name": "Other",
            "description": "Local RAG system",
            "command": "other-tool",
            "args": [],
            "env": {},
            "autoApprove": ["search"],
        }
        _clear_errors()
        configs = {"opencode.json": opencode, "claude-code.json": claude}
        cross_config_consistency(configs)
        errors = [str(e) for e in global_errors]
        assert any("tool" in e.lower() or "mismatch" in e.lower() or "same set" in e.lower() for e in errors), \
            f"Expected tool set mismatch error, got: {errors}"


# ============================================================================
# 9. Old format validation (detects deprecated mcpServers)
# ============================================================================

class TestOldFormatValidation:
    def test_old_format_detected(self):
        """Files with mcpServers should be flagged as deprecated."""
        config = {
            "mcpServers": {
                "corpus-kb": {
                    "command": "corpus-kb",
                    "args": [],
                }
            }
        }
        errors = _run_validator(validate_old_format, config, "opencode.json.bak")
        assert any("deprecated" in e.lower() or "mcpServers" in e for e in errors), \
            f"Expected deprecated format error, got: {errors}"

    def test_new_format_passes_old_validator(self):
        """Files already migrated to mcp should pass the old format validator."""
        config = {
            "mcp": {
                "corpus-kb": {
                    "command": ["corpus-kb"],
                }
            }
        }
        errors = _run_validator(validate_old_format, config, "opencode.json")
        assert errors == [], f"Migrated config should pass, got: {errors}"


# ============================================================================
# 10. Edge cases
# ============================================================================

class TestEdgeCases:
    def test_empty_config_opencode_fails(self, valid_opencode_config):
        errors = _run_validator(validate_opencode_format, {})
        assert len(errors) > 0, "Empty config should fail"

    def test_empty_config_claude_fails(self):
        errors = _run_validator(validate_claude_code_format, {}, "claude-code.json")
        assert len(errors) > 0, "Empty config should fail"

    def test_empty_config_cursor_fails(self):
        errors = _run_validator(validate_cursor_format, {}, "cursor.json")
        assert len(errors) > 0, "Empty config should fail"

    def test_non_dict_autoApprove_fails_opencode(self, valid_opencode_config):
        config = copy.deepcopy(valid_opencode_config)
        config["mcp"]["corpus-kb"]["autoApprove"] = "search"
        errors = _run_validator(validate_opencode_format, config)
        assert any("autoApprove" in e and ("list" in e.lower() or "array" in e.lower()) for e in errors), \
            f"Expected autoApprove type error, got: {errors}"

    def test_non_dict_autoApprove_fails_claude(self, valid_claude_config):
        config = copy.deepcopy(valid_claude_config)
        config["mcpServers"]["corpus-kb"]["autoApprove"] = "search"
        errors = _run_validator(validate_claude_code_format, config, "claude-code.json")
        assert any("autoApprove" in e and ("list" in e.lower() or "array" in e.lower()) for e in errors), \
            f"Expected autoApprove type error, got: {errors}"

    def test_missing_type_fails_opencode(self, valid_opencode_config):
        config = copy.deepcopy(valid_opencode_config)
        del config["mcp"]["corpus-kb"]["type"]
        errors = _run_validator(validate_opencode_format, config)
        assert any("type" in e.lower() for e in errors), f"Expected 'type' error, got: {errors}"

    def test_missing_environment_fails_opencode(self, valid_opencode_config):
        config = copy.deepcopy(valid_opencode_config)
        del config["mcp"]["corpus-kb"]["environment"]
        errors = _run_validator(validate_opencode_format, config)
        assert any("environment" in e.lower() for e in errors), f"Expected 'environment' error, got: {errors}"
