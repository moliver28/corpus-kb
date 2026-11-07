"""Marker safety and file writers: stubs, themes, summaries (issue #355).

``write-themes`` writes only between the ``corpus:begin`` and
``corpus:end`` markers in ``analysis/themes.md`` and never opens
``analysis/codes.md``. ``stubs`` never overwrite. ``apply-summary`` is
byte-stable on a second run.
"""

from __future__ import annotations

import sys
from pathlib import Path

PLUGIN = str(Path(__file__).resolve().parents[1])
if PLUGIN not in sys.path:
    sys.path.insert(0, PLUGIN)

import uxr_files  # noqa: E402

# ---------------------------------------------------------------------------
# Stubs
# ---------------------------------------------------------------------------


def source(slug: str, path: str, class_: str = "participant_session") -> dict:
    return {
        "slug": slug,
        "path": path,
        "class": class_,
        "confirmed": 1,
        "doc_id": None,
        "artifact_date": "2026-10-30",
    }


def test_stub_has_the_pinned_front_matter(tmp_path: Path) -> None:
    written = uxr_files.write_stubs(tmp_path, [source("p1", "interviews/p1.md")], "2026-11-07")
    assert len(written) == 1
    text = (tmp_path / "sources" / "corpus" / "p1.md").read_text(encoding="utf-8")
    assert (
        "---\n"
        "slug: p1\n"
        "canonical_url: interviews/p1.md\n"
        "artifact_date: 2026-10-30\n"
        "retrieved_at: 2026-11-07\n"
        "---\n"
    ) in text
    assert "[S:p1]" in text


def test_stubs_never_overwrite(tmp_path: Path) -> None:
    target = tmp_path / "sources" / "corpus" / "p1.md"
    target.parent.mkdir(parents=True)
    target.write_text("keep me\n", encoding="utf-8")
    written = uxr_files.write_stubs(tmp_path, [source("p1", "interviews/p1.md")], "2026-11-07")
    assert written == []
    assert target.read_text(encoding="utf-8") == "keep me\n"


def test_internal_meeting_source_gets_a_stub_too(tmp_path: Path) -> None:
    written = uxr_files.write_stubs(
        tmp_path, [source("team", "meetings/team.md", "internal_meeting")], "2026-11-07"
    )
    assert written == [tmp_path / "sources" / "corpus" / "team.md"]


# ---------------------------------------------------------------------------
# Markers
# ---------------------------------------------------------------------------


def test_replace_marked_keeps_outside_text_byte_for_byte() -> None:
    original = "Intro line.\ncorpus:begin\nold\ncorpus:end\nTail line.\n"
    out = uxr_files.replace_marked(original, "- H1: Supports")
    assert out == "Intro line.\ncorpus:begin\n- H1: Supports\ncorpus:end\nTail line.\n"


def test_replace_marked_appends_when_no_markers() -> None:
    out = uxr_files.replace_marked("Just some notes.\n", "- H1: Supports")
    assert out == "Just some notes.\ncorpus:begin\n- H1: Supports\ncorpus:end\n"


# ---------------------------------------------------------------------------
# write-themes
# ---------------------------------------------------------------------------


def test_write_themes_only_touches_themes_md(tmp_path: Path) -> None:
    analysis = tmp_path / "analysis"
    analysis.mkdir()
    (analysis / "codes.md").write_text("codebook, do not touch\n", encoding="utf-8")
    (analysis / "themes.md").write_text("human notes\n", encoding="utf-8")
    uxr_files.write_themes(tmp_path, "Theme one: exports confuse people.\n")
    assert (analysis / "themes.md").read_text(encoding="utf-8") == (
        "human notes\ncorpus:begin\nTheme one: exports confuse people.\ncorpus:end\n"
    )
    assert (analysis / "codes.md").read_text(encoding="utf-8") == (
        "codebook, do not touch\n"
    )


def test_write_themes_replaces_only_the_marked_block(tmp_path: Path) -> None:
    analysis = tmp_path / "analysis"
    analysis.mkdir()
    (analysis / "themes.md").write_text(
        "human notes\ncorpus:begin\nold theme\ncorpus:end\nfooter\n", encoding="utf-8"
    )
    uxr_files.write_themes(tmp_path, "new theme\n")
    text = (analysis / "themes.md").read_text(encoding="utf-8")
    assert text == (
        "human notes\ncorpus:begin\nnew theme\ncorpus:end\nfooter\n"
    )


def test_write_themes_never_accepts_another_target(tmp_path: Path) -> None:
    # The function signature has no target parameter, so analysis/codes.md
    # is unreachable by design. Pin the signature.
    import inspect

    parameters = list(inspect.signature(uxr_files.write_themes).parameters)
    assert parameters == ["root", "text"]


# ---------------------------------------------------------------------------
# apply-summary
# ---------------------------------------------------------------------------


def summary_setup(tmp_path: Path) -> tuple[Path, Path]:
    root = tmp_path / "study"
    interviews = root / "interviews"
    interviews.mkdir(parents=True)
    summary = interviews / "p1-summary.md"
    summary.write_text(
        "Analyst notes.\nH1: Supports (researcher saw it).\n", encoding="utf-8"
    )
    return root, summary


def test_apply_summary_writes_block_and_skips_researcher_qids(
    tmp_path: Path,
) -> None:
    root, summary = summary_setup(tmp_path)
    actions = uxr_files.apply_summaries(
        root,
        {"p1": ["- H1: Supports 2", "- RQ1: the export flow needs an undo"]},
    )
    assert actions == ["replaced: interviews/p1-summary.md"]
    text = summary.read_text(encoding="utf-8")
    assert "- H1: Supports 2" not in text  # the researcher line covers H1
    assert "- RQ1: the export flow needs an undo" in text
    assert "corpus:begin" in text and "corpus:end" in text
    assert "Analyst notes." in text
    assert "H1: Supports (researcher saw it)." in text


def test_apply_summary_is_byte_stable_on_a_second_run(tmp_path: Path) -> None:
    root, summary = summary_setup(tmp_path)
    blocks = {"p1": ["- RQ1: the export flow needs an undo"]}
    uxr_files.apply_summaries(root, blocks)
    first = summary.read_bytes()
    uxr_files.apply_summaries(root, blocks)
    assert summary.read_bytes() == first


def test_apply_summary_skips_missing_summaries(tmp_path: Path) -> None:
    root, _summary = summary_setup(tmp_path)
    actions = uxr_files.apply_summaries(root, {"ghost": ["- H1: Supports 1"]})
    assert actions == ["missing: interviews/ghost-summary.md"]


def test_summary_block_text_is_deterministic() -> None:
    lines = uxr_files.summary_block_text({"H1": "Supports", "RQ1": "a finding"})
    text = "\n".join(lines)
    assert text.index("RQ1") < text.index("H1")
    assert text.startswith("- RQ1: a finding")
