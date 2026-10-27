"""Profile loader + registry precedence tests (Living Corpus W5)."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

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
from corpus_kb.profiles.loader import (
    PROFILE_EXTRACTION_MODES,
    PROFILE_VALIDATE_MODES,
    parse_profile,
)

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
    from corpus_kb.ingestion import registry

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


# -- Audience data types + the validate key (unified-ingestion Wave 6)


@pytest.mark.parametrize("data_type", ["product", "engineering", "design"])
def test_audience_data_types_accepted(profiles_dir: Path, data_type: str) -> None:
    (profiles_dir / "aud.yaml").write_text(f"name: aud\ndata_type: {data_type}\n", encoding="utf-8")
    config = _config(profiles_dir, ingestion={"profile": "aud"})
    profile = load_profile(config)
    assert profile is not None
    assert profile.data_type == data_type


def test_validate_modes_stay_in_lockstep() -> None:
    """profiles never imports the tools layer; this test pins the two sets."""
    from corpus_kb.tools.pipeline.validate import MODES

    assert frozenset(MODES) == PROFILE_VALIDATE_MODES


def test_validate_key_defaults_to_off() -> None:
    profile = parse_profile(yaml.safe_load("name: p\n"), "p", "profiles/p.yaml")
    assert profile.validate_mode == "off"


def test_validate_key_parsed() -> None:
    raw = yaml.safe_load("name: p\nvalidate: advisory\n")
    profile = parse_profile(raw, "p", "profiles/p.yaml")
    assert profile.validate_mode == "advisory"


def test_bad_validate_mode_rejected() -> None:
    raw = yaml.safe_load("name: p\nvalidate: strict\n")
    with pytest.raises(ValidationError, match="validate mode"):
        parse_profile(raw, "p", "profiles/p.yaml")


def test_bare_off_reads_as_boolean_and_is_rejected() -> None:
    """YAML 1.1 reads a bare ``off`` as false; the loader refuses it."""
    raw = yaml.safe_load("name: p\nvalidate: off\n")
    with pytest.raises(ValidationError, match="validate mode"):
        parse_profile(raw, "p", "profiles/p.yaml")


def test_shipped_starter_profiles_parse() -> None:
    """The shipped starters stay valid: names, data types, validate modes."""
    repo_profiles = Path(__file__).resolve().parents[1] / "profiles"
    expected = {
        "starter-interview": ("interview", "off"),
        "starter-product": ("product", "advisory"),
        "starter-engineering": ("engineering", "advisory"),
        "starter-design": ("design", "advisory"),
    }
    for name, (data_type, validate_mode) in expected.items():
        path = repo_profiles / f"{name}.yaml"
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
        profile = parse_profile(raw, name, str(path))
        assert profile.data_type == data_type
        assert profile.validate_mode == validate_mode


# -- The calibrate block (unified-ingestion Wave 7)


def _calib_profile(profiles_dir: Path, block: str) -> object:
    (profiles_dir / "cal.yaml").write_text(
        f"name: cal\ndata_type: mixed\n{block}", encoding="utf-8"
    )
    return load_profile(_config(profiles_dir, ingestion={"profile": "cal"}))


def test_calibrate_block_parsed(profiles_dir: Path) -> None:
    profile = _calib_profile(
        profiles_dir,
        "calibrate:\n  candidates: [default, markdown_sections]\n"
        "  sample: sample/\n  golden: golden.jsonl\n  k: 7\n  min_overlap: 0.6\n",
    )
    assert profile is not None
    spec = profile.calibrate
    assert spec is not None
    assert spec.candidates == ("default", "markdown_sections")
    assert spec.sample == "sample/"
    assert spec.golden == "golden.jsonl"
    assert spec.k == 7
    assert spec.min_overlap == 0.6


def test_calibrate_block_defaults(profiles_dir: Path) -> None:
    profile = _calib_profile(
        profiles_dir,
        "calibrate:\n  candidates: [default]\n  sample: s.txt\n  golden: g.jsonl\n",
    )
    assert profile is not None and profile.calibrate is not None
    assert profile.calibrate.k == 10
    assert profile.calibrate.min_overlap == 0.5


def test_no_calibrate_block_is_none(profiles_dir: Path) -> None:
    profile = _calib_profile(profiles_dir, "extraction:\n  mode: zero_shot\n")
    assert profile is not None and profile.calibrate is None


def test_calibrate_empty_candidates_rejected(profiles_dir: Path) -> None:
    with pytest.raises(ValidationError, match="candidates"):
        _calib_profile(profiles_dir, "calibrate:\n  candidates: []\n  sample: s\n  golden: g\n")


def test_calibrate_bad_k_rejected(profiles_dir: Path) -> None:
    with pytest.raises(ValidationError, match="k"):
        _calib_profile(
            profiles_dir, "calibrate:\n  candidates: [default]\n  sample: s\n  golden: g\n  k: 0\n"
        )


def test_calibrate_min_overlap_out_of_range_rejected(profiles_dir: Path) -> None:
    with pytest.raises(ValidationError, match="min_overlap"):
        _calib_profile(
            profiles_dir,
            "calibrate:\n  candidates: [default]\n  sample: s\n  golden: g\n  min_overlap: 1.5\n",
        )


def test_calibrate_sample_must_be_a_string(profiles_dir: Path) -> None:
    with pytest.raises(ValidationError, match="sample"):
        _calib_profile(
            profiles_dir, "calibrate:\n  candidates: [default]\n  sample: 5\n  golden: g\n"
        )


def test_calibrate_unknown_key_rejected(profiles_dir: Path) -> None:
    with pytest.raises(ValidationError, match=r"unknown keys.*bogus"):
        _calib_profile(
            profiles_dir,
            "calibrate:\n  candidates: [default]\n  sample: s\n  golden: g\n  bogus: 1\n",
        )


def test_calibrate_duplicate_candidates_rejected(profiles_dir: Path) -> None:
    with pytest.raises(ValidationError, match="repeats: default"):
        _calib_profile(
            profiles_dir,
            "calibrate:\n  candidates: [default, default]\n  sample: s\n  golden: g\n",
        )
