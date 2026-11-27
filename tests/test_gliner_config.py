"""extraction.gliner config plumbing: both config.yaml copies carry the
pinned block, and validate_configs.py accepts valid blocks and rejects
malformed ones."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from corpus_kb._setup.validate_configs import (
    GLINER_MENTION_TYPES,
    load_yaml,
    validate_all,
    validate_extraction_gliner,
)
from corpus_kb.config import get_default_config
from corpus_kb.extraction import DEFAULT_GLINER_REVISION

ROOT = Path(__file__).resolve().parent.parent

_PINNED_TYPES = ["person", "organization", "location", "date", "money", "product"]
# The HF commit sha of fastino/gliner2-large-v1's tip (2026-11-27, issue #86
# item 4). A branch default floats and would silently change the model behind
# the eval promotion gate.
_PINNED_GLINER_REVISION = "d7aa8a2850d175a9227ff80358ea380723d03ad2"


def _gliner_block(config: dict[str, Any]) -> dict[str, Any]:
    extraction = config["extraction"]
    assert isinstance(extraction, dict)
    gliner = extraction["gliner"]
    assert isinstance(gliner, dict)
    return gliner


def test_root_and_packaged_configs_carry_pinned_gliner_block() -> None:
    """Both config.yaml copies and every code default carry the pinned sha."""
    for relative in ("config.yaml", Path("src") / "corpus_kb" / "config.yaml"):
        config = load_yaml(ROOT / relative)
        gliner = _gliner_block(config)
        assert gliner["enabled"] is False
        assert gliner["model"] == "fastino/gliner2-large-v1"
        assert gliner["revision"] == _PINNED_GLINER_REVISION
        assert gliner["types"] == _PINNED_TYPES
    assert get_default_config()["extraction"]["gliner"]["revision"] == _PINNED_GLINER_REVISION
    assert DEFAULT_GLINER_REVISION == _PINNED_GLINER_REVISION


def test_mention_type_vocabulary_is_the_six_pinned_types() -> None:
    assert frozenset(_PINNED_TYPES) == GLINER_MENTION_TYPES


def test_validator_accepts_real_configs() -> None:
    for relative in ("config.yaml", Path("src") / "corpus_kb" / "config.yaml"):
        assert validate_extraction_gliner(load_yaml(ROOT / relative)) == []


def test_validator_tolerates_missing_block() -> None:
    assert validate_extraction_gliner({}) == []
    assert validate_extraction_gliner({"extraction": {}}) == []


def test_validator_rejects_bad_blocks() -> None:
    base = {
        "extraction": {
            "gliner": {
                "enabled": True,
                "model": "fastino/gliner2-large-v1",
                "revision": "main",
                "types": _PINNED_TYPES,
            }
        }
    }

    bad_enabled = {
        **base,
        "extraction": {"gliner": {**base["extraction"]["gliner"], "enabled": "yes"}},
    }
    errors = validate_extraction_gliner(bad_enabled)
    assert any("enabled" in e for e in errors)

    bad_type = {
        **base,
        "extraction": {"gliner": {**base["extraction"]["gliner"], "types": ["person", "vehicle"]}},
    }
    errors = validate_extraction_gliner(bad_type)
    assert any("vehicle" in e for e in errors)

    no_model = {
        **base,
        "extraction": {"gliner": {**base["extraction"]["gliner"], "model": ""}},
    }
    errors = validate_extraction_gliner(no_model)
    assert any("model" in e for e in errors)

    types_not_list = {
        **base,
        "extraction": {"gliner": {**base["extraction"]["gliner"], "types": "person"}},
    }
    errors = validate_extraction_gliner(types_not_list)
    assert any("types" in e for e in errors)

    bad_revision = {
        **base,
        "extraction": {"gliner": {**base["extraction"]["gliner"], "revision": 3}},
    }
    errors = validate_extraction_gliner(bad_revision)
    assert any("revision" in e for e in errors)


def test_validator_reports_non_mapping_blocks() -> None:
    errors = validate_extraction_gliner({"extraction": "nope"})
    assert any("extraction: must be a mapping" in e for e in errors)
    errors = validate_extraction_gliner({"extraction": {"gliner": False}})
    assert any("extraction.gliner: must be a mapping" in e for e in errors)


def test_validate_all_passes_on_this_repo() -> None:
    """The whole-repo validation (MCP configs + both config.yaml) is green."""
    assert validate_all(ROOT) == []
