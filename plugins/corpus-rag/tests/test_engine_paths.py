"""Tests for plugins/corpus-rag/engine_paths.py (resolution chain)."""

from __future__ import annotations

import importlib
import sys
from pathlib import Path
from types import ModuleType

import pytest

PLUGIN = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PLUGIN))


def load() -> ModuleType:
    sys.modules.pop("engine_paths", None)
    return importlib.import_module("engine_paths")


def make_dev_repo(tmp_path: Path) -> Path:
    """A fake repo root with plugins/corpus-rag inside it."""
    repo = tmp_path / "corpus-kb"
    plugin = repo / "plugins" / "corpus-rag"
    plugin.mkdir(parents=True)
    (repo / "pyproject.toml").write_text(
        '[project]\nname = "corpus-kb"\n', encoding="utf-8"
    )
    return plugin


def clear_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in (
        "CORPUS_KB_REPO",
        "CORPUS_KB_STATE_DIR",
        "CLAUDE_PLUGIN_DATA",
        "CLAUDE_PLUGIN_OPTION_ENGINE_ROOT",
    ):
        monkeypatch.delenv(name, raising=False)


def test_env_repo_wins(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    clear_env(monkeypatch)
    env_repo = tmp_path / "elsewhere"
    env_repo.mkdir(parents=True)
    monkeypatch.setenv("CORPUS_KB_REPO", str(env_repo))
    mod = load()
    assert mod.engine_root(plugin_root=Path("/unused")) == env_repo


def test_dev_checkout_detected(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    clear_env(monkeypatch)
    plugin = make_dev_repo(tmp_path)
    mod = load()
    assert mod.engine_root(plugin_root=plugin) == plugin.parent.parent


def test_dev_checkout_detected_with_default_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Regression: the module-level PLUGIN_ROOT must point at the plugin
    folder (one level up from this file), not at the plugins/ dir."""
    clear_env(monkeypatch)
    plugin = make_dev_repo(tmp_path)
    (plugin / "engine_paths.py").write_text("# fake module\n", encoding="utf-8")
    mod = load()
    monkeypatch.setattr(mod, "PLUGIN_ROOT", plugin)
    assert mod.engine_root() == plugin.parent.parent


def test_dev_checkout_rejected_without_pyproject(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    clear_env(monkeypatch)
    repo = tmp_path / "not-corpus-kb"
    plugin = repo / "plugins" / "corpus-rag"
    plugin.mkdir(parents=True)
    mod = load()
    assert mod.engine_root(plugin_root=plugin) is None


def test_state_engine_fallback(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    clear_env(monkeypatch)
    state = tmp_path / "state"
    (state / "engine").mkdir(parents=True)
    monkeypatch.setenv("CORPUS_KB_STATE_DIR", str(state))
    plugin = tmp_path / "lonely" / "plugins" / "corpus-rag"
    plugin.mkdir(parents=True)
    mod = load()
    assert mod.engine_root(plugin_root=plugin) == state / "engine"


def test_venv_cli_found(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    clear_env(monkeypatch)
    repo = tmp_path / "corpus-kb"
    venv_bin = repo / ".venv" / "bin"
    venv_bin.mkdir(parents=True)
    cli = venv_bin / "corpus-kb"
    cli.write_text("#!/bin/sh\n", encoding="utf-8")
    mod = load()
    assert mod.engine_cli(repo) == str(cli)


def test_cli_none_when_engine_missing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    clear_env(monkeypatch)
    monkeypatch.setenv("PATH", "")
    mod = load()
    assert mod.engine_cli(tmp_path / "missing") is None


def test_user_config_engine_root_wins(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The engine_root userConfig (CLAUDE_PLUGIN_OPTION_ENGINE_ROOT) is the
    first step of the resolution chain (issue #351)."""
    clear_env(monkeypatch)
    option_repo = tmp_path / "option-checkout"
    option_repo.mkdir()
    env_repo = tmp_path / "env-checkout"
    env_repo.mkdir()
    monkeypatch.setenv("CORPUS_KB_REPO", str(env_repo))
    monkeypatch.setenv("CLAUDE_PLUGIN_OPTION_ENGINE_ROOT", str(option_repo))
    mod = load()
    assert mod.engine_root(plugin_root=Path("/unused")) == option_repo


def test_user_config_engine_root_ignored_when_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    clear_env(monkeypatch)
    monkeypatch.setenv("CLAUDE_PLUGIN_OPTION_ENGINE_ROOT", str(tmp_path / "not-there"))
    plugin = make_dev_repo(tmp_path)
    mod = load()
    assert mod.engine_root(plugin_root=plugin) == plugin.parent.parent


def test_plugin_data_dir_env_then_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    clear_env(monkeypatch)
    mod = load()
    monkeypatch.setenv("CLAUDE_PLUGIN_DATA", str(tmp_path / "plugin-data"))
    assert mod.plugin_data_dir() == tmp_path / "plugin-data"
    monkeypatch.delenv("CLAUDE_PLUGIN_DATA")
    assert mod.plugin_data_dir() == Path.home() / ".corpus-kb" / "plugin"
