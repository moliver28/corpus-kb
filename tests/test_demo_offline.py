"""Offline tests for `corpus-kb research demo` (todo-19, r13 test split).

These NEVER call get_app()/Ollama/Postgres: they pin the bundled-corpus
contract (existence, parser/role/exchange compatibility, verbatim exemplars)
and the narration wiring (guide_copy sources + doc pointers that exist on
disk). The full pipeline run lives in test_demo_e2e.py under
requires_postgres + requires_ollama.
"""

from __future__ import annotations

import re
from pathlib import Path

from corpus_kb.domain.transcript import normalize_turn_text
from corpus_kb.research import demo as demo_mod
from corpus_kb.research import guide_copy
from corpus_kb.research.governance_report import (
    SCHEMA_VERSION,
    ResearchReport,
    novice_view,
)
from corpus_kb.research.linking import link_exchanges
from corpus_kb.research.roles import map_roles
from corpus_kb.research.transcript_parser import parse_transcript

ROOT = Path(__file__).resolve().parent.parent


def test_demo_corpus_exists_repo_root_relative() -> None:
    root = demo_mod.DEMO_CORPUS_ROOT
    assert root == ROOT / "docs" / "demo-corpus"
    assert root.is_dir()
    for name in demo_mod.TRANSCRIPTS:
        assert (root / name).is_file(), name
    assert (root / demo_mod.CODEBOOK_FILE).is_file()


def test_demo_codebook_loads_and_validates() -> None:
    codebook = demo_mod.load_demo_codebook()
    assert codebook["label"]
    assert len(codebook["codes"]) >= 2
    for code in codebook["codes"]:
        assert code["definition"]
        assert len(code["exemplars"]) >= 1


def test_exemplars_are_verbatim_codable_answer_turns() -> None:
    """The gold-view contract: exemplar text == a participant answer turn that
    adjacency linking assigns to an exchange (load_gold_views resolves gold
    through research_units joined on exchanges)."""
    for name in demo_mod.TRANSCRIPTS:
        parsed = parse_transcript(demo_mod.DEMO_CORPUS_ROOT / name)
        assert parsed.turns, name
        method = map_roles(parsed.turns)
        assert method == "auto", name
        exchanges = link_exchanges(parsed.turns)
        answer_seqs = {seq for ex in exchanges for seq in ex.a_unit_seqs}
        participants = [t for t in parsed.turns if t.role == "participant"]
        assert participants, name
        for turn in parsed.turns:
            if turn.role == "moderator":
                assert not turn.is_codable
        for exemplar in _exemplars():
            wanted = normalize_turn_text(exemplar)
            matches = [t for t in participants if normalize_turn_text(t.text) == wanted]
            for turn in matches:
                assert turn.is_codable, exemplar[:60]
                assert turn.role_in_exchange == "answer", exemplar[:60]
                assert turn.seq in answer_seqs, exemplar[:60]


def _exemplars() -> list[str]:
    codebook = demo_mod.load_demo_codebook()
    return [ex for code in codebook["codes"] for ex in code["exemplars"]]


def test_every_exemplar_matches_some_transcript_turn() -> None:
    corpus_texts = {
        normalize_turn_text(turn.text)
        for name in demo_mod.TRANSCRIPTS
        for turn in parse_transcript(demo_mod.DEMO_CORPUS_ROOT / name).turns
    }
    for exemplar in _exemplars():
        assert normalize_turn_text(exemplar) in corpus_texts, exemplar[:60]


def test_demo_stages_narrate_from_guide_copy_with_existing_docs() -> None:
    stages = demo_mod.demo_stages()
    assert [s.name for s in stages] == [
        "ingest",
        "codebook",
        "coding",
        "review",
        "report",
        "ask",
    ]
    for stage in stages:
        assert stage.explain
        assert stage.explain in vars(guide_copy).values(), stage.name
        path_part, _, anchor = stage.doc.partition("#")
        doc = ROOT / path_part
        assert doc.is_file(), stage.doc
        if anchor:
            slugs = _heading_slugs(doc.read_text(encoding="utf-8"))
            assert anchor in slugs, stage.doc


def _heading_slugs(text: str) -> set[str]:
    slugs = set()
    for line in text.splitlines():
        match = re.match(r"^#{1,6}\s+(.*)$", line)
        if match:
            slug = re.sub(r"[^\w\s-]", "", match.group(1).lower())
            slugs.add(re.sub(r"[\s]+", "-", slug).strip())
    return slugs


def test_report_doc_anchors_cover_plain_language_and_exist() -> None:
    doc = ROOT / guide_copy.REPORT_DOC_PATH
    slugs = _heading_slugs(doc.read_text(encoding="utf-8"))
    assert set(guide_copy.REPORT_DOC_ANCHORS) == {
        "isr",
        "coverage",
        "residual",
        "tau_res",
        "stability",
        "overlap",
        "conflicts",
        "keywords",
        "keywords_provisional",
        "irr",
        "g3",
        "conformal",
        "manifest",
        "missing_codes",
        "backlog",
    }
    for anchor in guide_copy.REPORT_DOC_ANCHORS.values():
        assert anchor in slugs, anchor


def test_novice_view_links_every_section() -> None:
    report = _minimal_report()
    view = novice_view(report)
    links = view["doc_links"]
    assert set(links) == set(view["plain_language"])
    for section, link in links.items():
        assert link.startswith("docs/understanding-your-report.md#"), section

    import io
    from contextlib import redirect_stdout

    from corpus_kb.cli import _print_report

    buffered = io.StringIO()
    with redirect_stdout(buffered):
        _print_report(report, guide_copy.LEVEL_NOVICE)
    output = buffered.getvalue()
    for section, link in links.items():
        assert link in output, section
        assert f"guide[{section}]: {link}" in output, section


def _minimal_report() -> ResearchReport:
    return ResearchReport(
        schema_version=SCHEMA_VERSION,
        generated_at="2026-10-07T00:00:00Z",
        level=guide_copy.LEVEL_NOVICE,
        tenant_id="00000000-0000-0000-0000-000000000001",
        codebook_version_id="00000000-0000-0000-0000-00000000000a",
        codebook_label="test",
        isr_pooled=0.0,
        isr_by_source_type={},
        run_stop={},
        coverage_explicit=0.0,
        coverage_qdep=0.0,
        gray_zone_share=0.0,
        tier3_disagreement_rate=0.0,
        link_accuracy={},
        review_backlog={"assignments": 0},
        kappa_alpha={},
        residual={"pooled": {"R": 0.0}},
        coverage_curve={},
        deductive_coverage_note="",
        missing_codes={"candidates": []},
        cluster_stability={"stable": True},
        overlap={"flagged_pairs": []},
        keywords={"conflicts": []},
        g3_audit={},
        conformal={},
        run_manifest={},
        codebook_diff={},
    )


def test_demo_module_is_cli_only_no_mcp_tool_registered() -> None:
    """r13: the demo registers NO MCP tool (validate-configs untouched)."""
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "surface_registry_offline", ROOT / "src" / "corpus_kb" / "surface_registry.py"
    )
    assert spec is not None and spec.loader is not None
    reg = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(reg)
    row = next(s for s in reg.SURFACES if s.name == "research-demo")
    assert row.mcp_tool == ""
    assert row.cli_path == "research demo"


def test_demo_fail_fast_gate_blocks_abstaining_embedders() -> None:
    """run_demo must refuse to run when the embedder would abstain (a demo
    that dead-ends at review teaches nothing); the refusal text carries the
    runnable fix (guide_copy is the sole teaching-prose source)."""
    pgml_absent = {
        "embedding": {"provider": "pgml", "model": "pgml"},
        "research": {"embedder": {"dimensions": 1024}},
    }
    assert demo_mod.embedder_blocked(pgml_absent, pgml_installed=False)
    sub_1024 = {
        "embedding": {"provider": "ollama", "model": "nomic-embed-text", "dimensions": 768},
        "research": {"embedder": {"dimensions": 1024}},
    }
    assert demo_mod.embedder_blocked(sub_1024, pgml_installed=None)
    healthy = {
        "embedding": {
            "provider": "ollama",
            "model": "qwen3-embedding:8b-q8_0",
            "dimensions": 4096,
        },
        "research": {"embedder": {"dimensions": 1024}},
    }
    assert not demo_mod.embedder_blocked(healthy, pgml_installed=None)
    assert "embedding.provider" in guide_copy.DEMO_NO_EMBEDDINGS
    assert "qwen3-embedding" in guide_copy.DEMO_NO_EMBEDDINGS


def test_config_reference_table_covers_every_research_key() -> None:
    """r13: docs/research.md's pinned 3-column table must cover EVERY
    ``research.*`` key in get_default_config (column-1 parse) -- and nothing
    stale -- so new keys cannot land undocumented or drift away."""
    from typing import cast

    from corpus_kb.config import get_default_config

    def _walk(prefix: str, block: dict[str, object]) -> set[str]:
        keys: set[str] = set()
        for name, value in block.items():
            key = f"{prefix}.{name}"
            if isinstance(value, dict):
                keys |= _walk(key, cast(dict[str, object], value))
            else:
                keys.add(key)
        return keys

    research_block = cast(
        dict[str, object], cast(dict[str, object], get_default_config()).get("research", {})
    )
    expected = _walk("research", research_block)
    assert expected, "get_default_config lost the research block"

    documented: set[str] = set()
    for line in (ROOT / "docs" / "research.md").read_text(encoding="utf-8").splitlines():
        match = re.match(r"^\|\s*`([^`]+)`", line)
        if match:
            documented.add(match.group(1))
    missing = expected - documented
    stale = documented - expected
    assert not missing, f"research keys undocumented in docs/research.md: {sorted(missing)}"
    assert not stale, f"table rows absent from get_default_config: {sorted(stale)}"
