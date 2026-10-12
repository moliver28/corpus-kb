"""Release enforcement config is LIVE: lockstep + guard consumption (U24).

Pins the three-way lockstep (config.py defaults, root config.yaml, packaged
config.yaml) for the enforcement/consensus blocks, and proves the config
value actually reaches the deduction guard (the pre-staged -> live flip).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, cast

import pytest
import yaml

from corpus_kb.config import get_default_config, load_config

ROOT = Path(__file__).resolve().parent.parent


def _yaml_config(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as handle:
        return cast(dict[str, Any], yaml.safe_load(handle) or {})


def _research_block(config: dict[str, Any]) -> dict[str, Any]:
    return cast(dict[str, Any], config.get("research") or {})


# ---------------------------------------------------------------------------
# Lockstep: the three config locations carry identical values
# ---------------------------------------------------------------------------


def test_enforcement_and_consensus_blocks_are_in_three_way_lockstep() -> None:
    defaults = _research_block(get_default_config())
    root = _research_block(_yaml_config(ROOT / "config.yaml"))
    packaged = _research_block(_yaml_config(ROOT / "src" / "corpus_kb" / "config.yaml"))
    for block in ("release", "consensus", "cache"):
        assert defaults.get(block) == root.get(block) == packaged.get(block), (
            f"research.{block} drifted across config.py / config.yaml / packaged config.yaml"
        )
    release_defaults = cast(dict[str, Any], defaults.get("release") or {})
    assert release_defaults.get("enforcement") == "warn"
    assert release_defaults.get("default_profile") == "team-codebook"
    consensus_defaults = cast(dict[str, Any], defaults.get("consensus") or {})
    assert consensus_defaults.get("runs") == 3
    assert consensus_defaults.get("min_run_fraction") == 0.5
    assert consensus_defaults.get("similarity_threshold") == 0.8


# ---------------------------------------------------------------------------
# The key is LIVE: config flows into the deduction guard
# ---------------------------------------------------------------------------


def test_guard_reads_enforcement_from_loaded_config(tmp_path: Path) -> None:
    from corpus_kb.research.deduction_guard import release_guard_settings

    strict = tmp_path / "config-enforce.yaml"
    strict.write_text(
        "research:\n  release:\n    enforcement: enforce\n    default_profile: high-assurance\n",
        encoding="utf-8",
    )
    settings = release_guard_settings(load_config(str(strict)))
    assert settings.enforcement == "enforce"
    assert settings.default_profile == "high-assurance"

    # Default install (packaged defaults, no file): warn, never stricter.
    settings = release_guard_settings({"research": {}})
    assert settings.enforcement == "warn"


def test_warn_default_never_blocks_unreleased_versions() -> None:
    from uuid import UUID

    from corpus_kb.research.deduction_guard import (
        ReleaseGuardSettings,
        decide_guard,
    )

    decision = decide_guard(
        None, ReleaseGuardSettings(), UUID("00000000-0000-0000-0000-000000000003")
    )
    assert decision.action == "run"
    assert decision.warnings


def test_loaded_default_config_carries_the_full_research_block() -> None:
    config = load_config(str(ROOT / "config.yaml"))
    research = _research_block(config)
    for key in (
        "release",
        "consensus",
        "alt_test",
        "prompt_stability",
        "routing",
        "audit",
        "review",
        "labeling",
        "judgment_distribution",
    ):
        assert key in research, f"loaded config lost research.{key}"


# ---------------------------------------------------------------------------
# U42 cache semantics: deductive on, inductive off, --no-cache drops the key
# ---------------------------------------------------------------------------


def test_cache_settings_default_posture_from_config() -> None:
    from corpus_kb.research.exact_cache import load_cache_settings

    config = load_config(str(ROOT / "config.yaml"))
    cache = load_cache_settings(cast(dict[str, Any], config))
    assert cache.allows("deductive") is True
    assert cache.allows("inductive") is False
    no_cache = load_cache_settings({})  # --no-cache drops the block entirely
    assert no_cache.allows("deductive") is True  # module default
    disabled = load_cache_settings({"cache": {"enabled": False}})
    assert disabled.allows("deductive") is False


def test_cache_settings_reject_wrong_types() -> None:
    from corpus_kb.research.exact_cache import load_cache_settings

    with pytest.raises(ValueError, match="enabled"):
        load_cache_settings({"cache": {"enabled": "yes-please"}})


# ---------------------------------------------------------------------------
# U35 doctor check: judgment_distribution.use_mean verification
# ---------------------------------------------------------------------------


def test_doctor_judgment_distribution_check_honest_statuses() -> None:
    from corpus_kb._setup.doctor_research import judgment_distribution_check

    ok = judgment_distribution_check(dict(get_default_config()))
    assert ok.status == "ok"  # default off needs no probabilities

    enabled = judgment_distribution_check(
        {"research": {"judgment_distribution": {"use_mean": True}}}
    )
    assert enabled.status == "warn"
    assert "not_evaluable" in enabled.detail  # unknown exposure is never "ok"

    missing = judgment_distribution_check({"research": {}})
    assert missing.status == "ok"  # the documented default (off) applies
    assert "off" in missing.detail

    bad_type = judgment_distribution_check(
        {"research": {"judgment_distribution": {"use_mean": "sure"}}}
    )
    assert bad_type.status == "warn"

    bad_block = judgment_distribution_check({"research": {"judgment_distribution": "off"}})
    assert bad_block.status == "warn"
