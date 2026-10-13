"""U29 inductive-baseline smoke tests: offline, deterministic, fixture-driven.

Runs each script's ``main`` on the committed fixture corpus (meaningful path),
on a two-document temp corpus (honest ``insufficient_data`` path), and in
markdown mode. Asserts exit 0 and parseable output; also checks the verbatim
recovery-baseline caveat reaches ``--help`` and that the pluggable LLM-stage
seams (TaxonomyInducer / ConceptHypothesizer) are honored. Total runtime is a
few seconds; no Ollama, no network, no Postgres.

The scripts live under ``scripts/`` (not an installed package), so they are
imported by extending ``sys.path`` — the same mechanism the scripts use to
import their shared ``_inductive_common`` helper.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Protocol

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import _inductive_common as common
import bench_gatos_baseline as gatos
import bench_lloom_baseline as lloom
import bench_tntllm_baseline as tntllm

ROOT = Path(__file__).resolve().parent.parent
FIXTURES = ROOT / "tests" / "fixtures" / "inductive_baselines"

CAVEAT = (
    "GATOS-style validation uses synthetic planted themes, so results are "
    "recovery baselines, NOT proof of validity on real interviews."
)


class BaselineModule(Protocol):
    """The shared CLI surface every U29 baseline script exposes."""

    def main(self, argv: Sequence[str] | None = None) -> int: ...

    def parse_args(self, argv: Sequence[str]) -> argparse.Namespace: ...


BASELINES: list[BaselineModule] = [gatos, tntllm, lloom]


def _run_json(
    mod: BaselineModule, argv: Sequence[str], capsys: pytest.CaptureFixture[str]
) -> dict[str, object]:
    exit_code = mod.main(list(argv))
    assert exit_code == 0
    loaded = json.loads(capsys.readouterr().out)
    assert isinstance(loaded, dict)
    return loaded


@pytest.mark.parametrize("mod", BASELINES)
def test_help_carries_caveat_verbatim(
    mod: BaselineModule, capsys: pytest.CaptureFixture[str]
) -> None:
    with pytest.raises(SystemExit) as excinfo:
        mod.parse_args(["--help"])
    assert excinfo.value.code == 0
    assert CAVEAT in capsys.readouterr().out


@pytest.mark.parametrize("mod", BASELINES)
def test_meaningful_run_on_fixture_exits_zero(
    mod: BaselineModule, capsys: pytest.CaptureFixture[str]
) -> None:
    report = _run_json(mod, ["--input", str(FIXTURES)], capsys)
    assert report["status"] == "ok"
    assert report["corpus_status"] == "ok"
    assert report["document_count"] == 16
    assert str(report["label"]).startswith("local-machine baseline")
    assert report["recovery_baseline_caveat"] == CAVEAT
    assert report["seed"] == 20261012  # fixed default


@pytest.mark.parametrize("mod", BASELINES)
def test_insufficient_data_on_tiny_corpus(
    mod: BaselineModule, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    for src in sorted(FIXTURES.glob("*.md"))[:2]:
        (tmp_path / src.name).write_text(src.read_text(encoding="utf-8"), encoding="utf-8")
    report = _run_json(mod, ["--input", str(tmp_path)], capsys)
    assert report["status"] == "insufficient_data"
    assert report["corpus_status"] == "insufficient_data"
    assert "no numbers are reported" in str(report["reason"])
    assert "recovery" not in report  # no numbers fabricated


@pytest.mark.parametrize("mod", BASELINES)
def test_markdown_format_is_human_readable(
    mod: BaselineModule, capsys: pytest.CaptureFixture[str]
) -> None:
    exit_code = mod.main(["--input", str(FIXTURES), "--format", "markdown"])
    assert exit_code == 0
    out = capsys.readouterr().out
    assert out.startswith("# ")
    assert CAVEAT in out


def test_gatos_recovery_against_planted_themes(capsys: pytest.CaptureFixture[str]) -> None:
    report = _run_json(gatos, ["--input", str(FIXTURES)], capsys)
    recovery = report["recovery"]
    assert isinstance(recovery, dict)
    assert recovery["ari_vs_planted"] > 0  # planted structure is recovered
    per_theme = recovery["per_theme"]
    assert isinstance(per_theme, dict)
    assert set(per_theme) == {"deployment", "pricing", "performance"}
    sweep = report["threshold_sweep"]
    assert isinstance(sweep, list) and sweep
    assert "oracle bound" in str(report["oracle_note"])


def test_tntllm_pluggable_inducer_is_honored(capsys: pytest.CaptureFixture[str]) -> None:
    docs = common.load_corpus(FIXTURES)

    def single_code_inducer(sample: Sequence[common.Document]) -> list[tntllm.CodeProposal]:
        return [
            tntllm.CodeProposal(
                name="one-code",
                definition="forced single code",
                terms=("deploy",),
                member_doc_ids=tuple(doc.doc_id for doc in sample),
            )
        ]

    report = tntllm.run_tntllm(docs, {}, seed=1, sample_frac=0.5, inducer=single_code_inducer)
    assert report["taxonomy_size"] == 1
    llm_stage = report["llm_stage"]
    assert isinstance(llm_stage, dict) and llm_stage["used"] is False


def test_lloom_pluggable_hypothesizer_is_honored() -> None:
    docs = common.load_corpus(FIXTURES)

    def fixed_hypothesizer(
        sample: Sequence[common.Document], corpus: Sequence[common.Document]
    ) -> list[lloom.Concept]:
        return [lloom.Concept(name="forced", terms=("deploy",))]

    report = lloom.run_lloom(
        docs, {}, seed=1, max_iterations=4, sample_size=4, hypothesizer=fixed_hypothesizer
    )
    concepts = report["concepts"]
    assert isinstance(concepts, list) and concepts
    first = concepts[0]
    assert isinstance(first, dict) and first["name"] == "forced"


def test_determinism_same_args_same_report(
    capsys: pytest.CaptureFixture[str],
) -> None:
    argv = ["--input", str(FIXTURES)]
    first = _run_json(lloom, argv, capsys)
    second = _run_json(lloom, argv, capsys)
    assert first == second


def test_ari_sanity() -> None:
    labels = ["a", "a", "b", "b", "c", "c"]
    assert common.adjusted_rand_index(labels, labels) == 1.0
    shuffled = ["a", "b", "a", "b", "c", "c"]
    assert common.adjusted_rand_index(labels, shuffled) < 1.0
