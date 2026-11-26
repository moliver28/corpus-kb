"""Version lockstep between the marketplace manifest and the plugin."""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

PLUGIN = Path(__file__).resolve().parents[1]
REPO = PLUGIN.parent.parent


def _read(path: Path) -> dict[str, object]:
    return json.loads(path.read_text(encoding="utf-8"))


def test_plugin_json_is_valid() -> None:
    meta = _read(PLUGIN / ".claude-plugin" / "plugin.json")
    assert meta["name"] == "corpus-rag"
    author = meta["author"]
    assert isinstance(author, dict) and author.get("name")
    config = meta["userConfig"]
    for key, spec in config.items():
        for field in ("type", "title", "description"):
            assert isinstance(spec.get(field), str) and spec[field], f"{key}.{field}"
    version = meta["version"]
    assert isinstance(version, str)


def test_uxr_report_tool_user_config_key_is_declared() -> None:
    """The UXR contract-test tool path ships as a userConfig option (#355)."""
    meta = _read(PLUGIN / ".claude-plugin" / "plugin.json")
    key = meta["userConfig"]["uxr_report_tool"]
    assert key["type"] == "string"
    assert key.get("sensitive") is not True


def test_user_config_keys_are_declared() -> None:
    """The study-registry userConfig keys (issue #351) ship in lockstep."""
    meta = _read(PLUGIN / ".claude-plugin" / "plugin.json")
    config = meta["userConfig"]
    for key in ("engine_root", "app_dsn_template", "superuser_dsn", "base_port",
                "default_backend"):
        assert key in config, f"missing userConfig key: {key}"
    assert config["app_dsn_template"]["sensitive"] is True
    assert config["superuser_dsn"]["sensitive"] is True
    assert "{database}" in config["app_dsn_template"]["default"]


def test_manifest_entry_matches_plugin_json() -> None:
    manifest = _read(REPO / ".claude-plugin" / "marketplace.json")
    entry = next(p for p in manifest["plugins"] if p["name"] == "corpus-rag")
    assert entry["source"] == "./plugins/corpus-rag"
    meta = _read(PLUGIN / ".claude-plugin" / "plugin.json")
    assert entry["version"] == meta["version"]
    assert (REPO / "plugins" / "corpus-rag").is_dir()


def test_manifest_gate_passes() -> None:
    gate = REPO / "scripts" / "plugin_manifest_gate.py"
    result = subprocess.run(
        [sys.executable, str(gate)],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_claude_plugin_validate_passes_when_claude_is_present() -> None:
    """Claude Code's own validator must exit 0 (issue #507).

    Skipped where the ``claude`` CLI is not installed; the manifest gate
    covers the same fields with the stdlib checks.
    """
    claude = shutil.which("claude")
    if claude is None:
        pytest.skip("claude CLI not on PATH")
    result = subprocess.run(
        [claude, "plugin", "validate", str(PLUGIN)],
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
