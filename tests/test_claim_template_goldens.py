"""Claim-template goldens (Living Corpus W7).

One golden row per shipped template: the canonical sentence, the
template id the classifier MUST return, and the minimum entity count
the validator demands. Every shipped template file must have a golden
(a template without a golden is an untested claim shape), and every
golden must classify exactly — the fallback ``statement`` shape
included.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from corpus_kb.statements import TemplateCatalogue, load_templates

TEMPLATES_DIR = Path(__file__).resolve().parents[1] / "config" / "claim_templates"
GOLDEN_PATH = Path(__file__).resolve().parent / "fixtures" / "claim_templates" / "golden.jsonl"


@pytest.fixture(scope="module")
def catalogue() -> TemplateCatalogue:
    return load_templates(TEMPLATES_DIR)


@pytest.fixture(scope="module")
def golden() -> list[dict[str, object]]:
    rows = [
        json.loads(line)
        for line in GOLDEN_PATH.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert rows, "golden fixture must not be empty"
    return rows


def pytest_generate_tests(metafunc: pytest.Metafunc) -> None:
    """One classify case per golden row (collection-time, so the fixture
    file can grow without touching this module)."""
    if "case" in metafunc.fixturenames:
        rows = [
            json.loads(line)
            for line in GOLDEN_PATH.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        metafunc.parametrize("case", range(len(rows)))


def test_every_shipped_template_has_a_golden(golden: list[dict[str, object]]) -> None:
    shipped = {path.stem for path in sorted(TEMPLATES_DIR.glob("*.yaml"))}
    covered = {str(row["template_id"]) for row in golden}
    assert shipped <= covered, f"templates without a golden: {sorted(shipped - covered)}"


def test_golden_ids_exist_in_catalogue(
    catalogue: TemplateCatalogue, golden: list[dict[str, object]]
) -> None:
    known = set(catalogue.ids())
    for row in golden:
        assert str(row["template_id"]) in known, f"unknown golden id: {row['template_id']}"


def test_golden_classifies_exactly(
    catalogue: TemplateCatalogue, golden: list[dict[str, object]], case: int
) -> None:
    row = golden[case]
    assert catalogue.classify(str(row["text"])) == str(row["template_id"]), (
        f"golden {row['template_id']!r} misclassified: {row['text']!r}"
    )


def test_golden_min_entities_match_catalogue(
    catalogue: TemplateCatalogue, golden: list[dict[str, object]]
) -> None:
    for row in golden:
        spec = catalogue.spec(str(row["template_id"]))
        assert spec.min_entities == int(row["min_entities"]), (
            f"golden min_entities drifted for {row['template_id']!r}"
        )
