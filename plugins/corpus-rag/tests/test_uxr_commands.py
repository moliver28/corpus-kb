"""Command-level behavior for the 13 ``corpus-rag uxr`` commands (issue #355).

The commands run through the real ``cli_spec`` parser and handler map,
so wiring and behavior get tested together. No test calls the network
or a model; engine reads are monkeypatched at the seam.
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import pytest

PLUGIN = str(Path(__file__).resolve().parents[1])
if PLUGIN not in sys.path:
    sys.path.insert(0, PLUGIN)

import cli_spec  # noqa: E402
import gate_state  # noqa: E402
import uxr_commands  # noqa: E402
import uxr_store  # noqa: E402

SPEC = {
    "ticket": "UX1-275",
    "research_questions": ["What breaks in the export flow?"],
    "hypotheses": ["H1: The export failure explains churn."],
    "analysis_questions": ["A1: Does the nav redesign hold?"],
}
UNIT_TEXT = "The export button worked on the second try, but the icon was unclear."


def run(argv: list[str]) -> tuple[int, str]:
    import contextlib
    import io

    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer):
        code = cli_spec.HANDLERS[argv[0]](cli_spec.parse_argv(argv))
    return code, buffer.getvalue()


def setup_study(tmp_path: Path, monkeypatch) -> tuple[Path, Path]:
    monkeypatch.setenv("CLAUDE_PLUGIN_DATA", str(tmp_path / "plugin-data"))
    root = tmp_path / "study"
    (root / "interviews").mkdir(parents=True, exist_ok=True)
    (root / ".claude").mkdir(parents=True, exist_ok=True)
    (root / ".claude" / "corpus.json").write_text(
        json.dumps({"slug": "ux1", "uxr": {"ticket": "UX1-275"}}), encoding="utf-8"
    )
    (root / "00-overview.md").write_text("Study UX1-275 plan.\n", encoding="utf-8")
    (root / "interviews" / "p1.md").write_text(
        "**Interviewer**: hi\n**Dana (PM)**: hello\n**Participant A**: ok\n", encoding="utf-8"
    )
    spec = root / "UX1-275.spec.json"
    spec.write_text(json.dumps(SPEC), encoding="utf-8")
    monkeypatch.chdir(root)
    return root, spec


# ---------------------------------------------------------------------------
# consent
# ---------------------------------------------------------------------------


def test_consent_unknown_means_local_only(tmp_path: Path, monkeypatch) -> None:
    setup_study(tmp_path, monkeypatch)
    code, out = run(["uxr", "consent"])
    assert code == 1
    assert "local only" in out


def test_consent_reports_the_setup_answer(tmp_path: Path, monkeypatch) -> None:
    setup_study(tmp_path, monkeypatch)
    gate_state.set_local_only(Path.cwd(), False)
    code, out = run(["uxr", "consent"])
    assert code == 0
    assert "cloud" in out
    gate_state.set_local_only(Path.cwd(), True)
    code, out = run(["uxr", "consent"])
    assert code == 1


# ---------------------------------------------------------------------------
# ingest-plan: spec id and question ids
# ---------------------------------------------------------------------------


def test_spec_id_is_16_char_sha256_of_the_spec_file(
    tmp_path: Path, monkeypatch
) -> None:
    _root, spec = setup_study(tmp_path, monkeypatch)
    code, out = run(["uxr", "ingest-plan", "--spec", str(spec)])
    assert code == 0
    spec_id = hashlib.sha256(spec.read_bytes()).hexdigest()[:16]
    assert spec_id in out
    conn = uxr_store.connect("ux1")
    assert uxr_store.get_study(conn)["id"] == spec_id


def test_ingest_plan_stores_rq_h_and_a_ids(tmp_path: Path, monkeypatch) -> None:
    _root, spec = setup_study(tmp_path, monkeypatch)
    run(["uxr", "ingest-plan", "--spec", str(spec)])
    conn = uxr_store.connect("ux1")
    ids = [q["id"] for q in uxr_store.questions(conn)]
    assert ids == ["RQ1", "H1", "A1"]
    assert uxr_store.get_study(conn)["ticket"] == "UX1-275"


def test_ingest_plan_registers_the_post_search_command(
    tmp_path: Path, monkeypatch
) -> None:
    _root, spec = setup_study(tmp_path, monkeypatch)
    run(["uxr", "ingest-plan", "--spec", str(spec)])
    import post_search

    assert "uxr post-search" in post_search.commands()


# ---------------------------------------------------------------------------
# check-quote
# ---------------------------------------------------------------------------


def test_check_quote_command_reports_the_class(tmp_path: Path, monkeypatch) -> None:
    setup_study(tmp_path, monkeypatch)
    code, out = run(
        ["uxr", "check-quote", "--quote", "export button worked",
         "--unit-text", UNIT_TEXT]
    )
    assert code == 0
    assert "exact" in out
    code, out = run(
        ["uxr", "check-quote", "--quote", "the moon is cheese",
         "--unit-text", UNIT_TEXT]
    )
    assert code == 1
    assert "fail" in out


# ---------------------------------------------------------------------------
# classify, confirm-classes, stubs
# ---------------------------------------------------------------------------


def test_classify_prints_rows_and_the_confirm_payload(
    tmp_path: Path, monkeypatch
) -> None:
    root, _spec = setup_study(tmp_path, monkeypatch)
    roster = root / "roster.txt"
    roster.write_text("Participant A\n", encoding="utf-8")
    staff = root / "staff.txt"
    staff.write_text("Dana (PM)\nInterviewer\n", encoding="utf-8")
    code, out = run(
        ["uxr", "classify", "--dir", str(root), "--roster", str(roster),
         "--staff", str(staff)]
    )
    assert code == 0
    assert "participant_session" in out
    assert "00-overview.md" in out and "plan" in out
    assert "unmatched" in out  # never guess: unmatched files are shown
    payload_line = next(line for line in out.splitlines() if line.startswith("payload:"))
    rows = json.loads(payload_line[len("payload: "):])
    classes = {r["path"]: r["class"] for r in rows}
    assert classes["interviews/p1.md"] == "participant_session"


def test_confirm_classes_only_stores_sessions_and_meetings(
    tmp_path: Path, monkeypatch
) -> None:
    setup_study(tmp_path, monkeypatch)
    run(["uxr", "ingest-plan", "--spec", "UX1-275.spec.json"])
    payload = json.dumps(
        [
            {"path": "interviews/p1.md", "class": "participant_session"},
            {"path": "00-overview.md", "class": "plan"},
        ]
    )
    code, _out = run(["uxr", "confirm-classes", payload])
    assert code == 0
    conn = uxr_store.connect("ux1")
    stored = {r["slug"] for r in uxr_store.sources(conn)}
    assert stored == {"p1"}


def test_stubs_command_writes_front_matter(tmp_path: Path, monkeypatch) -> None:
    root, _spec = setup_study(tmp_path, monkeypatch)
    run(["uxr", "ingest-plan", "--spec", "UX1-275.spec.json"])
    run(["uxr", "confirm-classes", json.dumps([{"path": "interviews/p1.md",
                                               "class": "participant_session"}])])
    code, _out = run(["uxr", "stubs", "--dir", str(root)])
    assert code == 0
    stub = root / "sources" / "corpus" / "p1.md"
    assert stub.is_file()
    text = stub.read_text(encoding="utf-8")
    assert "canonical_url: interviews/p1.md" in text
    assert "retrieved_at:" in text


# ---------------------------------------------------------------------------
# The synthetic end-to-end run
# ---------------------------------------------------------------------------


def end_to_end(tmp_path: Path, monkeypatch) -> tuple[int, str, Path]:
    root, _spec = setup_study(tmp_path, monkeypatch)
    run(["uxr", "ingest-plan", "--spec", "UX1-275.spec.json"])
    run(["uxr", "confirm-classes", json.dumps([{"path": "interviews/p1.md",
                                               "class": "participant_session"}])])
    run(["uxr", "stubs", "--dir", str(root)])
    rows = json.dumps(
        [
            {
                "session": "p1",
                "unit_id": "H1",
                "stance": "Supports",
                "quote": "export button worked",
                "unit_text": UNIT_TEXT,
                "rationale": "said it worked",
            },
            {
                "session": "p1",
                "unit_id": "RQ1",
                "stance": "the export flow needs an undo",
                "quote": "worked on the second try",
                "unit_text": UNIT_TEXT,
                "rationale": "second attempt succeeded",
            },
        ]
    )
    code, _out = run(["uxr", "validate-rows", rows, "--store"])
    assert code == 0
    code, roll = run(["uxr", "rollup", "--dir", str(root)])
    return code, roll, root


def test_rollup_is_deterministic_across_runs(tmp_path: Path, monkeypatch) -> None:
    code, first, root = end_to_end(tmp_path, monkeypatch)
    assert code == 0
    second_code, second = run(["uxr", "rollup", "--dir", str(root)])
    assert second_code == 0
    assert first == second
    assert "H1: Supports" in first
    assert "RQ1" in first


def test_validate_rows_stores_and_insights_filter(
    tmp_path: Path, monkeypatch
) -> None:
    end_to_end(tmp_path, monkeypatch)
    monkeypatch.setattr(uxr_commands, "engine_document_count", lambda slug: 4)
    code, out = run(["uxr", "insights"])
    assert code == 0
    assert "[S:p1]" in out
    assert "Ingested: 4" in out and "analyzed: 1" in out
    code, out = run(["uxr", "insights", "--question", "H1"])
    assert "RQ1" not in out
    code, out = run(["uxr", "insights", "--stance", "Supports", "--format", "json"])
    data = json.loads(out[out.index("{"):])
    assert data["rows"][0]["stance"] == "Supports"
    assert data["rows"][0]["source"] == "p1"


# ---------------------------------------------------------------------------
# contract-test and import
# ---------------------------------------------------------------------------


def test_contract_test_uses_the_evidence_file(tmp_path: Path, monkeypatch) -> None:
    root, _spec = setup_study(tmp_path, monkeypatch)
    run(["uxr", "ingest-plan", "--spec", "UX1-275.spec.json"])
    code, _out = run(
        ["uxr", "contract-test", "--evidence-file", "missing.json", "--dir", str(root)]
    )
    assert code == 1
    evidence = tmp_path / "evidence.json"
    evidence.write_text(
        json.dumps(
            {
                "units": {},
                "researcher": {},
                "totals": {"verified": 0, "unverified": 0, "share": 0.0},
            }
        ),
        encoding="utf-8",
    )
    code, _out = run(
        ["uxr", "contract-test", "--evidence-file", str(evidence), "--dir", str(root)]
    )
    # An empty rollup matches empty evidence: parity holds.
    assert code == 0


def test_contract_test_reports_a_difference(tmp_path: Path, monkeypatch) -> None:
    root, _spec = setup_study(tmp_path, monkeypatch)
    end_to_end(tmp_path, monkeypatch)
    evidence = tmp_path / "evidence.json"
    evidence.write_text(
        json.dumps({"units": {"H1": {"Supports": 99}}, "researcher": {},
                    "totals": {"verified": 0, "unverified": 0, "share": 0.0}}),
        encoding="utf-8",
    )
    code, out = run(
        ["uxr", "contract-test", "--evidence-file", str(evidence), "--dir", str(root)]
    )
    assert code == 1
    assert "H1" in out


def test_import_copies_legacy_rows_once(tmp_path: Path, monkeypatch) -> None:
    setup_study(tmp_path, monkeypatch)
    run(["uxr", "ingest-plan", "--spec", "UX1-275.spec.json"])
    legacy = [
        {
            "session": "p1",
            "unit_id": "H1",
            "stance": "Supports",
            "quote": "export button worked",
            "quote_class": "exact",
            "rationale": "0.6.0 said so",
        }
    ]
    monkeypatch.setattr(uxr_commands, "fetch_legacy_rows", lambda slug: legacy)
    code, out = run(["uxr", "import"])
    assert code == 0
    conn = uxr_store.connect("ux1")
    assert len(uxr_store.rows(conn)) == 1
    assert uxr_store.imported(conn) is True
    code, out = run(["uxr", "import"])
    assert "already imported" in out


def test_import_without_legacy_data_stays_clean(tmp_path: Path, monkeypatch) -> None:
    setup_study(tmp_path, monkeypatch)
    monkeypatch.setattr(uxr_commands, "fetch_legacy_rows", lambda slug: None)
    code, out = run(["uxr", "import"])
    assert code == 0
    assert "no legacy" in out


# ---------------------------------------------------------------------------
# write-themes and apply-summary commands
# ---------------------------------------------------------------------------


def test_write_themes_command(tmp_path: Path, monkeypatch) -> None:
    root, _spec = setup_study(tmp_path, monkeypatch)
    (root / "analysis").mkdir()
    narrative = root / "narrative.md"
    narrative.write_text("Theme one: exports confuse people.\n", encoding="utf-8")
    code, _out = run(["uxr", "write-themes", "--narrative-file", str(narrative),
                      "--dir", str(root)])
    assert code == 0
    themes = root / "analysis" / "themes.md"
    assert themes.is_file()
    assert "corpus:begin" in themes.read_text(encoding="utf-8")


def test_apply_summary_command_writes_blocks(tmp_path: Path, monkeypatch) -> None:
    root, _spec = setup_study(tmp_path, monkeypatch)
    run(["uxr", "ingest-plan", "--spec", "UX1-275.spec.json"])
    run(["uxr", "confirm-classes", json.dumps([{"path": "interviews/p1.md",
                                               "class": "participant_session"}])])
    summary = root / "interviews" / "p1-summary.md"
    summary.write_text("Analyst notes.\n", encoding="utf-8")
    rows = json.dumps(
        [
            {
                "session": "p1",
                "unit_id": "H1",
                "stance": "Supports",
                "quote": "export button worked",
                "unit_text": UNIT_TEXT,
                "rationale": "said it worked",
            }
        ]
    )
    run(["uxr", "validate-rows", rows, "--store"])
    code, _out = run(["uxr", "apply-summary", "--dir", str(root)])
    assert code == 0
    text = summary.read_text(encoding="utf-8")
    assert "- H1: Supports" in text and "corpus:begin" in text
    first = summary.read_bytes()
    run(["uxr", "apply-summary", "--dir", str(root)])
    assert summary.read_bytes() == first


# ---------------------------------------------------------------------------
# Parser wiring
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "command",
    [
        "consent", "ingest-plan", "classify", "confirm-classes", "stubs",
        "check-quote", "validate-rows", "rollup", "contract-test",
        "write-themes", "apply-summary", "insights", "import",
    ],
)
def test_all_thirteen_commands_parse(command: str) -> None:
    minimal = {
        "ingest-plan": ["--spec", "UX1-275.spec.json"],
        "confirm-classes": ["[]"],
        "check-quote": ["--quote", "q", "--unit-text", "t"],
        "validate-rows": ["[]"],
        "write-themes": ["--narrative-file", "n.md"],
    }
    args = cli_spec.parse_argv(["uxr", command, *minimal.get(command, [])])
    assert args.uxr_command == command
