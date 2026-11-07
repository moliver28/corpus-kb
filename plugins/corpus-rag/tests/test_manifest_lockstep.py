"""Version lockstep between the marketplace manifest and the plugin."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

PLUGIN = Path(__file__).resolve().parents[1]
REPO = PLUGIN.parent.parent


def _read(path: Path) -> dict[str, object]:
    return json.loads(path.read_text(encoding="utf-8"))


def test_plugin_json_is_valid() -> None:
    meta = _read(PLUGIN / ".claude-plugin" / "plugin.json")
    assert meta["name"] == "corpus-rag"
    assert meta["version"] == "0.8.0"


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
