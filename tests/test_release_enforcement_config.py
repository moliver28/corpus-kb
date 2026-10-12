"""Release enforcement config is LIVE: lockstep + guard consumption (U24).

Pins the three-way lockstep (config.py defaults, root config.yaml, packaged
config.yaml) for the enforcement/consensus blocks, and proves the config
value actually reaches the deduction guard (the pre-staged -> live flip).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, cast
from uuid import UUID, uuid4

import pytest
import yaml

from corpus_kb.config import get_default_config, load_config
from corpus_kb.domain.codebook import CodebookVersion
from corpus_kb.research.release.manifest import ReleaseManifest
from corpus_kb.research.release.service import ReleaseService

ROOT = Path(__file__).resolve().parent.parent


class _Sink:
    """Minimal EventSink (the service only calls save)."""

    def save(self, aggregate: CodebookVersion) -> None:
        self.saved = getattr(self, "saved", [])
        self.saved.append(aggregate)


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


# ---------------------------------------------------------------------------
# F3: the RELEASE path applies the same config key (not the profile default)
# ---------------------------------------------------------------------------


def _enforce_cfg(mode: str) -> dict[str, Any]:
    return {"research": {"release": {"enforcement": mode, "default_profile": "team-codebook"}}}


def _team_draft_release() -> tuple[CodebookVersion, _Sink, UUID]:
    aggregate = CodebookVersion(
        tenant_id=UUID("00000000-0000-0000-0000-000000000001"),
        label="v1-enforce",
        sha256="c" * 64,
        paradigm="deductive",
    )
    sink = _Sink()
    service = ReleaseService(sink, config=_enforce_cfg("warn"))
    release_id = uuid4()
    service.request_release(
        aggregate,
        release_id=release_id,
        codebook_id=uuid4(),
        profile_name="team-codebook",
        creator="alice",
        manifest=ReleaseManifest(
            release_id=str(release_id),
            codebook_id=str(uuid4()),
            codebook_version=str(uuid4()),
            codebook_sha256="d" * 64,
            profile={"name": "team-codebook"},
            method_declaration={},
            machine_profile={},
            created_at="t",
            codes=[{"name": "cost", "definition": "price concern"}],
        ),
        requested_at="t",
    )
    return aggregate, sink, release_id


def test_release_refuses_when_config_enforce_and_required_gates_fail() -> None:
    aggregate, _sink, release_id = _team_draft_release()
    service = ReleaseService(_Sink(), config=_enforce_cfg("enforce"))
    outcome = service.release(aggregate, release_id, approver="dana", released_at="t2")
    assert outcome["status"] == "blocked"
    assert outcome["enforcement"] == "enforce"
    assert "structured_output_enforcement" in outcome["unmet_required_gates"]
    # Nothing was released: no event, state unchanged.
    assert aggregate.releases[str(release_id)]["state"] == "draft_candidate"


def test_release_with_config_enforce_passes_when_required_gates_pass() -> None:
    from corpus_kb.research.release.profiles import resolve_profile

    aggregate, _sink, release_id = _team_draft_release()
    for gate_id in resolve_profile("team-codebook").required_gates:
        aggregate.record_gate_result(
            tenant_id=aggregate.tenant_id,
            release_id=release_id,
            gate_id=gate_id,
            status="pass",
            value=None,
            threshold=None,
            reason="ok",
            evidence_refs=[],
            evaluator={},
            evaluated_at="t",
        )
    service = ReleaseService(_Sink(), config=_enforce_cfg("enforce"))
    outcome = service.release(aggregate, release_id, approver="dana", released_at="t2")
    assert outcome["status"] == "released"
    assert outcome["enforcement"] == "enforce"
    assert aggregate.releases[str(release_id)]["state"] == "released"


def test_release_warn_config_releases_with_warnings() -> None:
    aggregate, _sink, release_id = _team_draft_release()
    service = ReleaseService(_Sink(), config=_enforce_cfg("warn"))
    outcome = service.release(aggregate, release_id, approver="dana", released_at="t2")
    assert outcome["status"] == "released"
    assert outcome["enforcement"] == "warn"
    assert outcome["warnings"], "unmet gates under warn release WITH warnings"
    assert aggregate.releases[str(release_id)]["state"] == "released"


def test_release_off_config_releases_without_warnings() -> None:
    aggregate, _sink, release_id = _team_draft_release()
    service = ReleaseService(_Sink(), config=_enforce_cfg("off"))
    outcome = service.release(aggregate, release_id, approver="dana", released_at="t2")
    assert outcome["status"] == "released"
    assert outcome["enforcement"] == "off"
    assert outcome["warnings"] == []


def test_release_service_uses_the_shared_reader_not_the_profile_field() -> None:
    """F3 root cause: the applied mode comes from release_guard_settings,
    never from the hardcoded per-profile ``warn``."""
    from corpus_kb.research.release.enforcement_settings import release_guard_settings
    from corpus_kb.research.release.profiles import resolve_profile

    # The premise: every shipped profile still defaults to warn.
    for name in ("team-codebook", "high-assurance", "exploratory"):
        assert resolve_profile(name).enforcement == "warn"
    cfg = _enforce_cfg("enforce")
    service = ReleaseService(_Sink(), config=cfg)
    assert service._release_enforcement() == "enforce"
    assert service._release_enforcement() == release_guard_settings(cfg).enforcement
    with pytest.raises(ValueError, match="enforcement"):
        ReleaseService(_Sink(), config=_enforce_cfg("strict"))._release_enforcement()


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
