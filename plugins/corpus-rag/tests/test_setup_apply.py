"""Behavior tests for the journaled setup steps (issue #353).

Order, resume after failure, ``--off`` and ``--off --all``, slug
collisions, the DuckDB branch, and the consent flag. ``run_cli`` is
stubbed everywhere: no test may run a real provision.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

PLUGIN = str(Path(__file__).resolve().parents[1])
if PLUGIN not in sys.path:
    sys.path.insert(0, PLUGIN)

import gate_state  # noqa: E402
import setup_apply  # noqa: E402
import setup_commands  # noqa: E402
import setup_journal  # noqa: E402
import setup_offer  # noqa: E402
import setup_steps  # noqa: E402

ANSWERS = {
    "Study name": "interviews",
    "Folders": ["interviews"],
    "Backend": "postgres",
    "Cloud model": "No",
    "Auto ingest": "Yes",
    "Permissions": "Yes",
    "Autorun": "Yes",
}


class CliRecorder:
    """A stub for ``setup_steps.run_cli`` that can fail chosen commands."""

    def __init__(self, fail_on: tuple[str, ...] = ()):
        self.calls: list[list[str]] = []
        self.fail_on = fail_on

    def __call__(self, args: list[str], timeout: int = 3600):
        self.calls.append(list(args))
        if args and args[0] in self.fail_on:
            return type("R", (), {"returncode": 1, "stdout": "", "stderr": "boom"})()
        return type("R", (), {"returncode": 0, "stdout": "ok", "stderr": ""})()

    def commands(self) -> list[str]:
        return [args[0] for args in self.calls]


@pytest.fixture()
def research_folder(tmp_path: Path, monkeypatch) -> Path:
    monkeypatch.setenv("CLAUDE_PLUGIN_DATA", str(tmp_path / "plugin-data"))
    monkeypatch.setenv("CORPUS_KB_STATE_DIR", str(tmp_path / "state"))
    folder = tmp_path / "Interviews"
    (folder / "interviews").mkdir(parents=True)
    (folder / "interviews" / "small.md").write_text("P1: a. P2: b.\n", encoding="utf-8")
    (folder / "interviews" / "big.md").write_text("P1: a. P2: b.\n" * 10, encoding="utf-8")
    return folder


@pytest.fixture()
def recorder(monkeypatch) -> CliRecorder:
    stub = CliRecorder()
    monkeypatch.setattr(setup_steps, "run_cli", stub)
    return stub


def applied_calls(recorder: CliRecorder) -> list[list[str]]:
    return [args for args in recorder.calls if args[0] in ("provision", "ingest")]


# ---------------------------------------------------------------------------
# Journal order and the happy path
# ---------------------------------------------------------------------------


def test_apply_runs_steps_in_order_and_writes_journal(
    research_folder: Path, recorder: CliRecorder
) -> None:
    journal = setup_apply.apply(research_folder, ANSWERS)
    assert [row["name"] for row in journal["steps"]] == list(setup_journal.STEP_ORDER)
    assert all(row["state"] == "done" for row in journal["steps"])
    on_disk = setup_journal.load(research_folder)
    assert on_disk is not None and on_disk["slug"] == "interviews"
    # database provisions, config seeds the smallest file
    assert applied_calls(recorder)[0][0] == "provision"
    assert applied_calls(recorder)[0][1] == "interviews"
    ingest_call = applied_calls(recorder)[1]
    assert ingest_call[0] == "ingest" and ingest_call[1].endswith("small.md")


def test_apply_writes_corpus_json_and_queues_the_rest(
    research_folder: Path, recorder: CliRecorder
) -> None:
    setup_apply.apply(research_folder, ANSWERS)
    marker = json.loads(
        (research_folder / ".claude" / "corpus.json").read_text(encoding="utf-8")
    )
    assert marker["slug"] == "interviews"
    assert marker["watch"] == ["interviews"]
    assert marker["auto_ingest"] is True
    rows = [
        json.loads(line)
        for line in (research_folder / ".claude" / "corpus-pending.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
    ]
    assert [Path(row["path"]).name for row in rows] == ["big.md"]


def test_apply_sets_gate_state_and_consent(research_folder: Path, recorder) -> None:
    setup_apply.apply(research_folder, ANSWERS)
    entry = gate_state.folder_entry(research_folder)
    assert entry["slug"] == "interviews"
    assert entry["enabled_by_setup"] is True
    assert entry["local_only"] is True  # Cloud model: No
    assert not setup_offer.sentinel_present(research_folder)


def test_allow_rules_written_and_uxr_rule(research_folder: Path, recorder) -> None:
    setup_apply.apply(research_folder, {**ANSWERS, "uxr": True})
    settings = json.loads(
        (research_folder / ".claude" / "settings.json").read_text(encoding="utf-8")
    )
    allow = settings["permissions"]["allow"]
    assert "Bash(corpus-rag search:*)" in allow
    assert "Bash(corpus-rag ask:*)" in allow
    assert "Bash(corpus-rag status:*)" in allow
    assert "Bash(uxr insights:*)" in allow


def test_engine_override_edits_the_overlay(research_folder: Path, recorder) -> None:
    import study_config

    setup_apply.apply(research_folder, ANSWERS)
    overlay = study_config.plugin_data_dir() / study_config.OVERLAY_FILENAME
    text = overlay.read_text(encoding="utf-8")
    assert "queue:" in text and "autorun: true" in text


def test_autorun_no_skips_the_overlay_edit(research_folder: Path, recorder) -> None:
    import study_config

    setup_apply.apply(research_folder, {**ANSWERS, "Autorun": "No"})
    overlay = study_config.plugin_data_dir() / study_config.OVERLAY_FILENAME
    assert not overlay.exists()


# ---------------------------------------------------------------------------
# Failure and resume
# ---------------------------------------------------------------------------


def test_resume_after_failure_skips_done_steps(
    research_folder: Path, recorder: CliRecorder
) -> None:
    recorder.fail_on = ("ingest",)
    with pytest.raises(setup_apply.StepError) as exc:
        setup_apply.apply(research_folder, ANSWERS)
    assert "config" in str(exc.value)
    failed = setup_journal.load(research_folder)
    assert failed["failed_step"] == "config"
    done = setup_journal.done_steps(failed)
    assert done == {"database", "allow_rules", "engine_override"}

    recorder.fail_on = ()
    journal = setup_apply.apply(research_folder, ANSWERS)
    names = [row["name"] for row in journal["steps"]]
    assert names.count("database") == 1  # not re-run
    assert names.count("config") == 2  # retried, now done
    assert journal["failed_step"] is None


def test_failed_provision_records_the_step(research_folder: Path, recorder) -> None:
    recorder.fail_on = ("provision",)
    with pytest.raises(setup_apply.StepError) as exc:
        setup_apply.apply(research_folder, ANSWERS)
    assert "database" in str(exc.value)
    journal = setup_journal.load(research_folder)
    assert journal["failed_step"] == "database"
    assert setup_journal.done_steps(journal) == set()


def test_apply_without_answers_uses_the_journal(
    research_folder: Path, recorder: CliRecorder
) -> None:
    recorder.fail_on = ("ingest",)
    with pytest.raises(setup_apply.StepError):
        setup_apply.apply(research_folder, ANSWERS)
    recorder.fail_on = ()
    # A plain apply call (no answers) resumes with the journal's answers.
    journal = setup_apply.apply(research_folder, {})
    assert journal["slug"] == "interviews"
    assert journal["answers"]["Study name"] == "interviews"
    assert setup_journal.done_steps(journal) == set(setup_journal.STEP_ORDER)
    assert setup_journal.pending_step(journal) is None


# ---------------------------------------------------------------------------
# Slug rule
# ---------------------------------------------------------------------------


def test_slug_collision_appends_sha1_hex(research_folder: Path, recorder) -> None:
    other = research_folder.parent / "Other"
    other.mkdir()
    gate_state.update_folder(other, {"slug": "interviews"})
    slug = setup_apply.slug_for_folder(research_folder, "interviews")
    assert slug.startswith("interviews_")
    suffix = slug.split("_", 1)[1]
    assert len(suffix) == 6
    int(suffix, 16)  # hex


def test_corpus_json_slug_wins_over_the_answer(research_folder: Path, recorder) -> None:
    (research_folder / ".claude").mkdir()
    (research_folder / ".claude" / "corpus.json").write_text(
        '{"slug": "from_marker"}', encoding="utf-8"
    )
    assert setup_apply.slug_for_folder(research_folder) == "from_marker"


def test_folder_name_slug_when_no_answer(research_folder: Path, recorder) -> None:
    assert setup_apply.slug_for_folder(research_folder) == "interviews"


# ---------------------------------------------------------------------------
# DuckDB branch
# ---------------------------------------------------------------------------


def test_duckdb_branch_provisions_with_the_backend(
    research_folder: Path, recorder: CliRecorder
) -> None:
    setup_apply.apply(research_folder, {**ANSWERS, "Backend": "duckdb"})
    provision = applied_calls(recorder)[0]
    assert provision[0] == "provision"
    assert "--backend" in provision and "duckdb" in provision
    entry = gate_state.folder_entry(research_folder)
    assert entry["slug"].startswith("interviews")


# ---------------------------------------------------------------------------
# setup --off
# ---------------------------------------------------------------------------


def test_off_undoes_rules_and_override_but_keeps_database(
    research_folder: Path, recorder: CliRecorder, monkeypatch
) -> None:
    from setup_commands import _off_folder

    setup_apply.apply(research_folder, ANSWERS)
    stopped: list[str] = []
    monkeypatch.setattr(
        setup_commands, "_stop_study", lambda slug: stopped.append(slug) or []
    )
    _off_folder(research_folder)
    settings = json.loads(
        (research_folder / ".claude" / "settings.json").read_text(encoding="utf-8")
    )
    allow = settings["permissions"]["allow"]
    assert "Bash(corpus-rag search:*)" not in allow
    marker = json.loads(
        (research_folder / ".claude" / "corpus.json").read_text(encoding="utf-8")
    )
    assert marker["enabled"] is False
    assert gate_state.folder_entry(research_folder)["declined"] is True
    assert gate_state.folder_entry(research_folder)["enabled_by_setup"] is False
    assert not setup_offer.sentinel_present(research_folder)
    assert stopped == ["interviews"]
    # The undo never touches the database: no cleanup command ran.
    assert applied_calls(recorder)[-1][0] == "ingest"


def test_off_all_covers_every_setup_folder(
    tmp_path: Path, monkeypatch, recorder
) -> None:
    monkeypatch.setenv("CLAUDE_PLUGIN_DATA", str(tmp_path / "plugin-data"))
    monkeypatch.setenv("CORPUS_KB_STATE_DIR", str(tmp_path / "state"))
    from setup_commands import _off_folder

    folders = []
    for index in range(2):
        folder = tmp_path / f"Study{index}"
        (folder / ".claude").mkdir(parents=True)
        (folder / ".claude" / "corpus.json").write_text(
            json.dumps({"slug": f"s{index}"}), encoding="utf-8"
        )
        (folder / ".claude" / "settings.json").write_text(
            json.dumps({"permissions": {"allow": list(setup_steps.ALLOW_RULES)}}),
            encoding="utf-8",
        )
        gate_state.update_folder(folder, {"enabled_by_setup": True, "slug": f"s{index}"})
        folders.append(folder)
    for folder in folders:
        _off_folder(folder)
    assert gate_state.folders_enabled_by_setup() == []
    for folder in folders:
        marker = json.loads(
            (folder / ".claude" / "corpus.json").read_text(encoding="utf-8")
        )
        assert marker["enabled"] is False
        settings = json.loads(
            (folder / ".claude" / "settings.json").read_text(encoding="utf-8")
        )
        assert setup_steps.ALLOW_RULES[0] not in settings["permissions"]["allow"]
