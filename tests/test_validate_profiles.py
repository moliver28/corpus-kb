"""validate-profiles gate tests (Living Corpus W5)."""

from __future__ import annotations

from pathlib import Path

from corpus_kb._setup.validate_profiles import validate_profiles

TEMPLATE_YAML = (
    "id: preference\n"
    "description: likes and dislikes\n"
    "signals:\n"
    '  - "\\\\b(i|we) like\\\\b"\n'
    "min_entities: 1\n"
    "roles: [subject, object]\n"
)


def _profile(name: str, extra: str = "") -> str:
    return (
        f"name: {name}\n"
        "description: interview study\n"
        "data_type: interview\n"
        "ingestion:\n"
        "  default: transcript\n"
        "templates:\n"
        "  - preference\n" + extra
    )


def _setup(tmp_path: Path) -> tuple[Path, Path]:
    profiles = tmp_path / "profiles"
    profiles.mkdir(exist_ok=True)
    templates = tmp_path / "claim_templates"
    templates.mkdir(exist_ok=True)
    (templates / "preference.yaml").write_text(TEMPLATE_YAML, encoding="utf-8")
    return profiles, templates


def test_clean_profile_passes(tmp_path: Path) -> None:
    profiles, templates = _setup(tmp_path)
    (profiles / "interviews.yaml").write_text(_profile("interviews"), encoding="utf-8")
    assert validate_profiles(profiles, templates) == []


def test_unknown_strategy_fails(tmp_path: Path) -> None:
    profiles, templates = _setup(tmp_path)
    (profiles / "bad.yaml").write_text(
        _profile("bad", "ingestion:\n  default: tarot\n"), encoding="utf-8"
    )
    errors = validate_profiles(profiles, templates)
    assert any("unknown ingestion strategy" in e for e in errors)


def test_unknown_template_id_fails(tmp_path: Path) -> None:
    profiles, templates = _setup(tmp_path)
    body = _profile("bad").replace("  - preference", "  - numerology")
    (profiles / "bad.yaml").write_text(body, encoding="utf-8")
    errors = validate_profiles(profiles, templates)
    assert any("claim-template catalogue" in e for e in errors)


def test_missing_seeds_file_fails(tmp_path: Path) -> None:
    profiles, templates = _setup(tmp_path)
    (profiles / "bad.yaml").write_text(
        _profile("bad", "seeds_file: ghosts.seeds.yaml\n"), encoding="utf-8"
    )
    errors = validate_profiles(profiles, templates)
    assert any("seeds file" in e for e in errors)


def test_broken_yaml_fails(tmp_path: Path) -> None:
    profiles, templates = _setup(tmp_path)
    (profiles / "bad.yaml").write_text("name: [unclosed\n", encoding="utf-8")
    errors = validate_profiles(profiles, templates)
    assert errors and "bad.yaml" in errors[0]


def test_missing_profiles_dir_is_clean(tmp_path: Path) -> None:
    assert validate_profiles(tmp_path / "absent", tmp_path) == []
