"""Offline tests: pinned report schema + guide_copy sole-source (todo 17)."""

from __future__ import annotations

import dataclasses
import re
from pathlib import Path

from corpus_kb.research import guide_copy
from corpus_kb.research.governance_report import (
    REQUIRED_FIELDS,
    SCHEMA_VERSION,
    ResearchReport,
    novice_view,
    to_dict,
    validate_required,
)


def _minimal_report() -> ResearchReport:
    return ResearchReport(
        schema_version=SCHEMA_VERSION,
        generated_at="t",
        level=guide_copy.LEVEL_EXPERT,
        tenant_id="t",
        codebook_version_id="v",
        codebook_label="v1",
        isr_pooled=0.1,
        isr_by_source_type={"interview": 0.1},
        run_stop={"should_stop": None},
        coverage_explicit=0.5,
        coverage_qdep=0.1,
        gray_zone_share=0.05,
        tier3_disagreement_rate=0.0,
        link_accuracy={"mean_link_score": 0.9},
        review_backlog={"assignments": 3},
        kappa_alpha={"halt": False, "tentative_codes": ["c1"]},
        residual={"pooled": {"R": 0.4}},
        coverage_curve={"R_slope": -0.1},
        deductive_coverage_note="reported separately",
        missing_codes={"candidates": [{"n_units": 5}]},
        cluster_stability={"stable": True},
        overlap={"flagged_pairs": [{"a": "1", "b": "2", "cos": 0.9}]},
        keywords={"conflicts": [{"term": "x"}]},
        g3_audit={"status": "not_available"},
        conformal={"status": "not_available"},
        run_manifest={"runs_recorded": 0},
        codebook_diff={},
    )


def test_every_required_field_present_on_built_report():
    payload = to_dict(_minimal_report())
    assert validate_required(payload) == []


def test_validate_required_flags_missing():
    payload = to_dict(_minimal_report())
    del payload["residual"]
    missing = validate_required(payload)
    assert "residual" in missing


def test_novice_view_traffic_lights_next_actions_from_flags():
    view = novice_view(_minimal_report())
    lights = dict(view["traffic_lights"])
    assert dict(lights["residual"])["level"] == "red"  # pooled R 0.4 > 0.20
    assert dict(lights["overlap"])["level"] == "red"  # flagged pair present
    assert dict(lights["irr"])["level"] == "yellow"  # tentative code present
    actions = view["next_actions"]
    assert isinstance(actions, list)
    assert all(isinstance(a, str) for a in actions)


def test_guide_copy_is_the_sole_prose_source():
    # Every plain-language string surfaced by the novice view must come from
    # guide_copy constants (r13 sole-source pin), i.e. the view never embeds
    # teaching prose inline.
    view = novice_view(_minimal_report())
    constants = {
        value
        for name, value in vars(guide_copy).items()
        if name.isupper() and isinstance(value, str)
    }
    lights = dict(view["traffic_lights"])
    plain = dict(view["plain_language"])
    messages = [dict(light)["message"] for light in lights.values()] + list(plain.values())
    for message in messages:
        assert message in constants


def test_guide_copy_purity_module_level_constants_only():
    source = Path(guide_copy.__file__).read_text(encoding="utf-8")
    assert not re.search(r"^\s*def ", source, flags=re.MULTILINE)
    assert "lambda" not in source
    assert "import" not in source.replace('"""', "", 2).split('"""', 2)[-1].split("\n", 1)[0]


def test_research_report_dataclass_field_count_pinned():
    # The r9/r10 required set is load-bearing: guard against silent removal.
    assert len(REQUIRED_FIELDS) == 20
    field_names = {f.name for f in dataclasses.fields(ResearchReport)}
    assert field_names >= REQUIRED_FIELDS
