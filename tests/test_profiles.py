"""Profile loader + registry precedence tests (Living Corpus W5)."""

from __future__ import annotations

from pathlib import Path

import pytest

from corpus_kb.errors import ValidationError
from corpus_kb.extraction.ml_backend import EXTRACTION_MODES
from corpus_kb.ingestion.registry import resolve_strategy
from corpus_kb.profiles import (
    IngestionProfile,
    load_profile,
    load_seeds,
    resolve_profile_name,
    safe_profile_path,
    with_profile_overrides,
)
from corpus_kb.profiles.loader import PROFILE_EXTRACTION_MODES

VALID = """
name: interviews
description: interview study
data_type: interview
ingestion:
  strategies:
    transcript: transcript
  default: transcript
extraction:
  mode: customized
templates:
  - preference
seeds_file: interviews.seeds.yaml
"""


@pytest.fixture
def profiles_dir(tmp_path: Path) -> Path:
    (tmp_path / "profiles").mkdir()
    (tmp_path / "profiles" / "interviews.yaml").write_text(VALID, encoding="utf-8")
    (tmp_path / "profiles" / "interviews.seeds.yaml").write_text(
        "exemplars:\n  - text: I like ice cream.\n    extractions: []\n",
        encoding="utf-8",
    )
    return tmp_path / "profiles"


def _config(profiles_dir: Path, **extra: object) -> dict[str, object]:
    return {"profiles": {"dir": str(profiles_dir)}, **extra}


def test_modes_stay_in_lockstep() -> None:
    """profiles never imports extraction; this test pins the two sets."""
    assert PROFILE_EXTRACTION_MODES == EXTRACTION_MODES


def test_load_profile_happy(profiles_dir: Path) -> None:
    config = _config(profiles_dir, ingestion={"profile": "interviews"})
    profile = load_profile(config)
    assert profile is not None
    assert profile.name == "interviews"
    assert profile.data_type == "interview"
    assert profile.strategies == {"transcript": "transcript"}
    assert profile.default_strategy == "transcript"
    assert profile.extraction_mode == "customized"
    assert profile.templates == ("preference",)


def test_load_profile_missing_file(profiles_dir: Path) -> None:
    config = _config(profiles_dir, ingestion={"profile": "nope"})
    with pytest.raises(ValidationError, match="not found"):
        load_profile(config)


def test_unknown_top_key_rejected(profiles_dir: Path) -> None:
    (profiles_dir / "bad.yaml").write_text("name: bad\nwat: 1\n", encoding="utf-8")
    config = _config(profiles_dir, ingestion={"profile": "bad"})
    with pytest.raises(ValidationError, match="unknown keys"):
        load_profile(config)


def test_name_mismatch_rejected(profiles_dir: Path) -> None:
    (profiles_dir / "other.yaml").write_text("name: interviews\n", encoding="utf-8")
    config = _config(profiles_dir, ingestion={"profile": "other"})
    with pytest.raises(ValidationError, match="declares name"):
        load_profile(config)


def test_bad_data_type_rejected(profiles_dir: Path) -> None:
    (profiles_dir / "bad2.yaml").write_text("name: bad2\ndata_type: gossip\n", encoding="utf-8")
    config = _config(profiles_dir, ingestion={"profile": "bad2"})
    with pytest.raises(ValidationError, match="data_type"):
        load_profile(config)


def test_no_profile_returns_none(profiles_dir: Path) -> None:
    assert load_profile(_config(profiles_dir)) is None


def test_override_wins_over_config(profiles_dir: Path) -> None:
    config = _config(profiles_dir, ingestion={"profile": "interviews"})
    profile = load_profile(config, override="interviews")
    assert profile is not None
    with pytest.raises(ValidationError):
        load_profile(config, override="missing")


def test_profile_name_must_be_plain(profiles_dir: Path) -> None:
    config = _config(profiles_dir, ingestion={"profile": "sub/dir"})
    with pytest.raises(ValidationError):
        load_profile(config)


def test_safe_profile_path_blocks_escape(tmp_path: Path) -> None:
    with pytest.raises(ValidationError):
        safe_profile_path(str(tmp_path), "sub/../../evil.yaml")
    with pytest.raises(ValidationError):
        safe_profile_path(str(tmp_path), ".hidden")
    assert safe_profile_path(str(tmp_path), "seeds/x.yaml") == str(tmp_path / "seeds" / "x.yaml")


def test_resolve_profile_name_validation(tmp_path: Path) -> None:
    assert resolve_profile_name({}) is None
    with pytest.raises(ValidationError):
        resolve_profile_name({"ingestion": {"profile": ""}})


def test_with_profile_overrides_merges_mode() -> None:
    profile = IngestionProfile(name="p", data_type="mixed", extraction_mode="customized")
    merged = with_profile_overrides({"extraction": {"mode": "zero_shot", "backend": "ml"}}, profile)
    assert merged["extraction"]["mode"] == "customized"
    assert merged["extraction"]["backend"] == "ml"
    assert with_profile_overrides({"extraction": {}}, None)["extraction"] == {}


def test_load_seeds(profiles_dir: Path) -> None:
    config = _config(profiles_dir, ingestion={"profile": "interviews"})
    profile = load_profile(config)
    assert profile is not None
    seeds = load_seeds(config, profile)
    assert seeds == [{"text": "I like ice cream.", "extractions": []}]


# -- Registry precedence: explicit > profile > config map > detection > default


class _FakeStrategy:
    name = "transcript"


def _patch_registry(monkeypatch: pytest.MonkeyPatch) -> None:
    import corpus_kb.ingestion.registry as registry

    monkeypatch.setattr(registry, "_REGISTRY", {"transcript": _FakeStrategy()})
    monkeypatch.setattr(registry, "_ready", True)


def test_profile_strategy_beats_config_map(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_registry(monkeypatch)
    profile = IngestionProfile(name="p", data_type="mixed", strategies={"text": "transcript"})
    config: dict[str, object] = {"ingestion": {"strategies": {"text": "default"}}}
    resolution = resolve_strategy("text", "f.txt", "head", config, profile=profile)
    assert resolution.basis == "profile"


def test_explicit_still_beats_profile(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_registry(monkeypatch)
    profile = IngestionProfile(name="p", data_type="mixed", strategies={"text": "transcript"})
    resolution = resolve_strategy(
        "text", "f.txt", "head", {}, strategy_override="transcript", profile=profile
    )
    assert resolution.basis == "explicit"


def test_profile_default_beats_config_default(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_registry(monkeypatch)
    profile = IngestionProfile(name="p", data_type="mixed", default_strategy="transcript")
    config: dict[str, object] = {"ingestion": {"default": "default"}}
    resolution = resolve_strategy("text", "f.txt", "head", config, profile=profile)
    assert resolution.basis == "default"
    assert resolution.strategy.name == "transcript"
