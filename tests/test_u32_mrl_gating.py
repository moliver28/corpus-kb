"""U32 MRL gating: the two-stage matryoshka arm refuses non-MRL embedders.

``search.matryoshka_enabled: true`` front-slices stored embeddings to
``matryoshka_dim`` — only sound for Matryoshka-trained models. The declared
flag is ``embedding.mrl_trained`` (default False): load_config RAISES on the
invalid combination (never a silent activation), validate_runtime_config
reports the same combination for the shipped YAMLs, and the default state
(arm off, flag off) loads cleanly with nomic-embed-text.
"""

from __future__ import annotations

from pathlib import Path
from typing import cast

import pytest
import yaml

from corpus_kb._setup import validate_configs
from corpus_kb.config import get_default_config, load_config, matryoshka_mrl_error

ROOT = Path(__file__).resolve().parents[1]


def _config_with(matryoshka_enabled: object, mrl_trained: object) -> dict[str, object]:
    """The default config with the two U32 keys set explicitly."""
    config = get_default_config()
    search = cast(dict[str, object], config["search"])
    embedding = cast(dict[str, object], config["embedding"])
    search["matryoshka_enabled"] = matryoshka_enabled
    embedding["mrl_trained"] = mrl_trained
    return config


def _write_config(tmp_path: Path, matryoshka_enabled: object, mrl_trained: object) -> Path:
    payload = {
        "search": {"matryoshka_enabled": matryoshka_enabled},
        "embedding": {"mrl_trained": mrl_trained},
    }
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump(payload))
    return path


class TestDefaults:
    """The shipped default: arm off, flag off (nomic-embed-text is not MRL)."""

    def test_default_config_declares_mrl_false(self):
        embedding = cast(dict[str, object], get_default_config()["embedding"])
        assert embedding["mrl_trained"] is False

    def test_default_config_declares_arm_off(self):
        search = cast(dict[str, object], get_default_config()["search"])
        assert search["matryoshka_enabled"] is False

    def test_default_combination_loads_cleanly(self):
        assert matryoshka_mrl_error(get_default_config()) is None
        assert load_config()  # the worktree config.yaml (arm off) loads


class TestLoadConfigGate:
    """The runtime gate: an enabled arm on a non-MRL embedder is refused."""

    def test_arm_on_non_mrl_raises(self, tmp_path: Path):
        path = _write_config(tmp_path, matryoshka_enabled=True, mrl_trained=False)
        with pytest.raises(ValueError, match="mrl_trained"):
            load_config(str(path))

    def test_refusal_is_honest_not_silent(self, tmp_path: Path):
        # The error names the models and the remedy — no silent degradation.
        path = _write_config(tmp_path, matryoshka_enabled=True, mrl_trained=False)
        with pytest.raises(ValueError) as excinfo:
            load_config(str(path))
        message = str(excinfo.value)
        assert "search.matryoshka_enabled: true requires embedding.mrl_trained: true" in message
        assert "qwen3-embedding" in message
        assert "nomic-embed-text" in message

    def test_arm_on_mrl_trained_loads(self, tmp_path: Path):
        path = _write_config(tmp_path, matryoshka_enabled=True, mrl_trained=True)
        config = load_config(str(path))
        search = cast(dict[str, object], config["search"])
        embedding = cast(dict[str, object], config["embedding"])
        assert search["matryoshka_enabled"] is True
        assert embedding["mrl_trained"] is True

    def test_arm_off_non_mrl_loads(self, tmp_path: Path):
        path = _write_config(tmp_path, matryoshka_enabled=False, mrl_trained=False)
        assert load_config(str(path))

    def test_error_helper_matches_loader(self):
        assert matryoshka_mrl_error(_config_with(True, False)) is not None
        assert matryoshka_mrl_error(_config_with(True, True)) is None
        assert matryoshka_mrl_error(_config_with(False, False)) is None
        # Truthy-but-not-True values (e.g. the string "yes" — a YAML mistake)
        # do NOT count as declaring MRL: only the literal boolean True.
        assert matryoshka_mrl_error(_config_with(True, "yes")) is not None


class TestValidateConfigs:
    """validate_runtime_config: presence+type of the key, and the cross-rule."""

    def test_key_declared(self):
        assert validate_configs.RUNTIME_CONFIG_KEYS["embedding.mrl_trained"] is bool

    def test_shipped_yamls_pass(self):
        for name in ("config.yaml", "src/corpus_kb/config.yaml"):
            cfg = validate_configs.load_yaml(ROOT / name)
            assert validate_configs.validate_runtime_config(cfg) == [], name

    def test_missing_key_fails(self):
        config = cast(dict[str, object], validate_configs.load_yaml(ROOT / "config.yaml"))
        embedding = cast(dict[str, object], config["embedding"])
        del embedding["mrl_trained"]
        errors = validate_configs.validate_runtime_config(config)
        assert any("missing required key 'embedding.mrl_trained'" in e for e in errors)

    def test_mistyped_key_fails(self):
        config = _config_with(False, "false")
        errors = validate_configs.validate_runtime_config(config)
        assert any("key 'embedding.mrl_trained' must be bool" in e for e in errors)

    def test_cross_field_bad_combination_reported(self):
        errors = validate_configs.validate_runtime_config(_config_with(True, False))
        assert any("requires embedding.mrl_trained: true" in e for e in errors)

    def test_cross_field_good_combination_clean(self):
        # Build from the shipped YAML: it carries every RUNTIME_CONFIG_KEY,
        # so == [] proves the cross-rule is the only thing being exercised.
        config = cast(dict[str, object], validate_configs.load_yaml(ROOT / "config.yaml"))
        embedding = cast(dict[str, object], config["embedding"])
        search = cast(dict[str, object], config["search"])
        embedding["mrl_trained"] = True
        search["matryoshka_enabled"] = True
        assert validate_configs.validate_runtime_config(config) == []
