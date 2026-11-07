"""Classification rules and speaker parsing (issue #355, behavior 0.6.0 pin).

Path rules run first, then speakers go against the roster and the staff
list. No model call. The study never calls a file internal when a line
may hide a speaker.
"""

from __future__ import annotations

import sys
from pathlib import Path

PLUGIN = str(Path(__file__).resolve().parents[1])
if PLUGIN not in sys.path:
    sys.path.insert(0, PLUGIN)

import uxr_classify  # noqa: E402


def make_file(root: Path, rel: str, text: str) -> Path:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


TRANSCRIPT = (
    "**Interviewer**: What broke first?\n"
    "**Dana (PM)**: The export stalled.\n"
    "**Participant A**: I gave up after a minute.\n"
    "**Interviewer**: Then what?\n"
)
MEETING = (
    "**Dana (PM)**: The export stall is our top bug.\n"
    "**Lee (Eng)**: We can ship a fix this week.\n"
    "**Dana (PM)**: Do it.\n"
)


# ---------------------------------------------------------------------------
# Speaker parsing
# ---------------------------------------------------------------------------


def test_parse_speakers_finds_bold_and_plain_labels() -> None:
    names, uncertain = uxr_classify.parse_speakers(TRANSCRIPT)
    assert names == ["interviewer", "dana (pm)", "participant a"]
    assert uncertain is False


def test_parse_speakers_flags_bold_lines_that_may_hide_a_speaker() -> None:
    # The bold line has no label colon; a speaker may hide in it.
    text = MEETING + "**we should also invite the researchers**\n"
    names, uncertain = uxr_classify.parse_speakers(text)
    assert names == ["dana (pm)", "lee (eng)"]
    assert uncertain is True


def test_parse_speakers_empty_text_has_no_speakers() -> None:
    assert uxr_classify.parse_speakers("") == ([], False)


# ---------------------------------------------------------------------------
# Path rules
# ---------------------------------------------------------------------------


def test_path_rules_excluded_plan_derived() -> None:
    assert uxr_classify.classify_rel("raw-transcripts/a.md") == "excluded"
    assert uxr_classify.classify_rel("interviews/participant-key.md") == "excluded"
    assert uxr_classify.classify_rel("00-overview.md") == "plan"
    assert uxr_classify.classify_rel("UX1-275.spec.json") == "plan"
    assert uxr_classify.classify_rel("analysis/themes.md") == "derived"
    assert uxr_classify.classify_rel("deliverables/report.md") == "derived"
    assert uxr_classify.classify_rel("interviews/p1-summary.md") == "derived"
    assert uxr_classify.classify_rel("interviews/p1-key-points.md") == "derived"


def test_path_rule_leaves_transcripts_and_meetings_open() -> None:
    assert uxr_classify.classify_rel("interviews/p1.md") is None
    assert uxr_classify.classify_rel("meetings/team.md") is None


# ---------------------------------------------------------------------------
# Full classification
# ---------------------------------------------------------------------------


def make_root(tmp_path: Path) -> tuple[Path, dict[str, Path]]:
    root = tmp_path / "study"
    root.mkdir()
    files = {
        "participant": make_file(root, "interviews/p1.md", TRANSCRIPT),
        "meeting": make_file(root, "meetings/team.md", MEETING),
        "overview": make_file(root, "00-overview.md", "Study UX1-275.\n"),
        "summary": make_file(root, "interviews/p1-summary.md", "Notes.\n"),
        "raw": make_file(root, "raw-transcripts/r1.md", TRANSCRIPT),
        "key": make_file(root, "interviews/participant-key.md", "A is Dana.\n"),
        "spec": make_file(root, "UX1-275.spec.json", "{}\n"),
    }
    return root, files


def test_classify_dir_sorts_every_file(tmp_path: Path) -> None:
    root, _files = make_root(tmp_path)
    roster = uxr_classify.load_names_from_text("Participant A\n")
    staff = uxr_classify.load_names_from_text("Dana (PM)\nLee (Eng)\nInterviewer\n")
    rows = uxr_classify.classify_dir(root, roster=roster, staff=staff)
    by_path = {r["path"]: r for r in rows}
    assert by_path["interviews/p1.md"]["class"] == "participant_session"
    assert by_path["meetings/team.md"]["class"] == "internal_meeting"
    assert by_path["00-overview.md"]["class"] == "plan"
    assert by_path["UX1-275.spec.json"]["class"] == "plan"
    assert by_path["interviews/p1-summary.md"]["class"] == "derived"
    assert by_path["raw-transcripts/r1.md"]["class"] == "excluded"
    assert by_path["interviews/participant-key.md"]["class"] == "excluded"
    # 00-overview.md has no speakers: unmatched by speakers is wrong; the
    # path rule wins, so it stays plan.
    assert by_path["00-overview.md"]["class"] == "plan"


def test_uncertain_line_blocks_internal_meeting(tmp_path: Path) -> None:
    root = tmp_path / "study"
    root.mkdir()
    make_file(
        root,
        "meetings/shaky.md",
        MEETING + "**maybe a participant joined here**\n",
    )
    roster = uxr_classify.load_names_from_text("Participant A\n")
    staff = uxr_classify.load_names_from_text("Dana (PM)\nLee (Eng)\n")
    rows = uxr_classify.classify_dir(root, roster=roster, staff=staff)
    by_path = {r["path"]: r for r in rows}
    row = by_path["meetings/shaky.md"]
    # A line may hide a speaker, so internal_meeting is never allowed.
    assert row["class"] == "participant_session"
    assert row["uncertain"] is True


def test_no_roster_or_staff_leaves_speaker_files_unmatched(tmp_path: Path) -> None:
    root, _files = make_root(tmp_path)
    rows = uxr_classify.classify_dir(root)
    by_path = {r["path"]: r for r in rows}
    assert by_path["interviews/p1.md"]["class"] == "unmatched"
    assert by_path["meetings/team.md"]["class"] == "unmatched"


def test_participant_beats_staff_when_both_appear(tmp_path: Path) -> None:
    root, _files = make_root(tmp_path)
    roster = uxr_classify.load_names_from_text("Participant A\n")
    staff = uxr_classify.load_names_from_text("Dana (PM)\nInterviewer\n")
    rows = uxr_classify.classify_dir(root, roster=roster, staff=staff)
    by_path = {r["path"]: r for r in rows}
    assert by_path["interviews/p1.md"]["class"] == "participant_session"


def test_classify_dir_is_sorted_and_stable(tmp_path: Path) -> None:
    root, _files = make_root(tmp_path)
    roster = uxr_classify.load_names_from_text("Participant A\n")
    staff = uxr_classify.load_names_from_text("Dana (PM)\nLee (Eng)\nInterviewer\n")
    first = [r["path"] for r in uxr_classify.classify_dir(root, roster, staff)]
    second = [r["path"] for r in uxr_classify.classify_dir(root, roster, staff)]
    assert first == sorted(first)
    assert first == second
