"""Issue #348 scope 2: CORPUS_KB_CONFIG_OVERLAY overlays.

``load_config`` deep-merges every overlay path (``os.pathsep``-split, in
order) after the main file and before the env overrides. A missing
overlay file is an error, never a silent skip.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import pytest
import yaml

from corpus_kb.config import config_overlay_paths, load_config
from corpus_kb.errors import ConfigError


@pytest.fixture
def isolated(monkeypatch: Any, tmp_path: Path) -> Path:
    """No repo config discovery and no stray overrides: cwd = tmp_path."""
    monkeypatch.chdir(tmp_path)
    for name in (
        "CORPUS_KB_CONFIG",
        "CORPUS_KB_CONFIG_OVERLAY",
        "CORPUS_KB_EMBEDDING_MODEL",
        "CORPUS_KB_DATABASE_URL",
    ):
        monkeypatch.delenv(name, raising=False)
    return tmp_path


def _write(path: Path, payload: dict[str, Any]) -> Path:
    path.write_text(yaml.safe_dump(payload), encoding="utf-8")
    return path


def test_overlay_changes_llm_model(isolated: Path, monkeypatch: Any) -> None:
    """The issue Check: one overlay key changes the resolved config."""
    overlay = _write(isolated / "overlay.yaml", {"llm": {"model": "qwen3:8b"}})
    monkeypatch.setenv("CORPUS_KB_CONFIG_OVERLAY", str(overlay))
    assert load_config()["llm"]["model"] == "qwen3:8b"


def test_overlays_merge_in_order(isolated: Path, monkeypatch: Any) -> None:
    first = _write(isolated / "a.yaml", {"llm": {"model": "a-model", "timeout_s": 30}})
    second = _write(isolated / "b.yaml", {"llm": {"model": "b-model"}})
    monkeypatch.setenv("CORPUS_KB_CONFIG_OVERLAY", os.pathsep.join([str(first), str(second)]))
    config = load_config()
    assert config["llm"]["model"] == "b-model"  # later overlay wins
    assert config["llm"]["timeout_s"] == 30  # earlier keys survive


def test_missing_overlay_is_an_error(isolated: Path, monkeypatch: Any) -> None:
    """A missing overlay file is an error, not a silent skip."""
    missing = isolated / "nope.yaml"
    monkeypatch.setenv("CORPUS_KB_CONFIG_OVERLAY", str(missing))
    with pytest.raises(ConfigError) as excinfo:
        load_config()
    assert str(missing) in str(excinfo.value)


def test_non_mapping_overlay_is_an_error(isolated: Path, monkeypatch: Any) -> None:
    """An overlay that is a YAML scalar or list is a config error."""
    overlay = isolated / "flat.yaml"
    overlay.write_text("just-a-string\n", encoding="utf-8")
    monkeypatch.setenv("CORPUS_KB_CONFIG_OVERLAY", str(overlay))
    with pytest.raises(ConfigError) as excinfo:
        load_config()
    assert "must hold a YAML mapping" in str(excinfo.value)


def test_overlay_deep_merges_nested_blocks(isolated: Path, monkeypatch: Any) -> None:
    overlay = _write(
        isolated / "overlay.yaml", {"retrieval": {"self_query": {"model": "qwen3:8b"}}}
    )
    monkeypatch.setenv("CORPUS_KB_CONFIG_OVERLAY", str(overlay))
    self_query = load_config()["retrieval"]["self_query"]
    assert self_query["model"] == "qwen3:8b"
    assert self_query["enabled"] is True  # sibling keys from the defaults survive


def test_env_overrides_win_over_overlay(isolated: Path, monkeypatch: Any) -> None:
    """Overlays merge BEFORE the env layer, so env still wins."""
    overlay = _write(isolated / "overlay.yaml", {"embedding": {"model": "overlay-model"}})
    monkeypatch.setenv("CORPUS_KB_CONFIG_OVERLAY", str(overlay))
    monkeypatch.setenv("CORPUS_KB_EMBEDDING_MODEL", "env-model")
    assert load_config()["embedding"]["model"] == "env-model"


def test_config_overlay_paths_split_on_pathsep(monkeypatch: Any) -> None:
    monkeypatch.setenv("CORPUS_KB_CONFIG_OVERLAY", os.pathsep.join(["a.yaml", "b.yaml", ""]))
    assert [path.name for path in config_overlay_paths()] == ["a.yaml", "b.yaml"]


def test_config_overlay_paths_unset(monkeypatch: Any) -> None:
    monkeypatch.delenv("CORPUS_KB_CONFIG_OVERLAY", raising=False)
    assert config_overlay_paths() == []
