"""Behavior tests for the setup-wave commands (issue #353).

``--questions`` writes nothing; snooze prints its date; ``pause`` and
``resume`` touch only ``enabled``; ``signals`` prints counts; the
setup router exits 2 without a mode.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

PLUGIN = str(Path(__file__).resolve().parents[1])
if PLUGIN not in sys.path:
    sys.path.insert(0, PLUGIN)

import gate_state  # noqa: E402
import setup_commands  # noqa: E402
import setup_controls  # noqa: E402
import setup_offer  # noqa: E402


class Args:
    """The argparse namespace the command bodies read."""

    def __init__(self, **kwargs):
        self.questions = False
        self.apply = False
        self.off = False
        self.all = False
        self.backend = None
        self.answers = None
        self.days = 7
        self.bundle = False
        self.__dict__.update(kwargs)


def isolate(tmp_path: Path, monkeypatch) -> Path:
    monkeypatch.setenv("CLAUDE_PLUGIN_DATA", str(tmp_path / "plugin-data"))
    monkeypatch.setenv("CORPUS_KB_STATE_DIR", str(tmp_path / "state"))
    return tmp_path / "folder"


def make_corpus_folder(tmp_path: Path, slug: str = "q3") -> Path:
    folder = tmp_path
    (folder / ".claude").mkdir(parents=True, exist_ok=True)
    (folder / ".claude" / "corpus.json").write_text(
        json.dumps({"slug": slug, "watch": ["interviews"]}), encoding="utf-8"
    )
    return folder


# ---------------------------------------------------------------------------
# setup --questions writes nothing
# ---------------------------------------------------------------------------


def test_questions_print_payload_and_write_nothing(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    folder = isolate(tmp_path, monkeypatch)
    folder.mkdir()
    (folder / "p1-transcript.md").write_text(
        "P1: a@example.com and Pat Q.\nP2: b.\nP1: c.\n", encoding="utf-8"
    )
    import preflight

    monkeypatch.setattr(
        preflight,
        "run",
        lambda backend=None: {
            "hard": [{"name": "engine root", "ok": True, "fix": "", "detail": "x"}],
            "soft": [{"name": "Ollama", "ok": False, "fix": "start it", "detail": "down"}],
        },
    )
    monkeypatch.chdir(folder)
    code = setup_commands.cmd_setup(Args(questions=True))
    out = capsys.readouterr().out
    payload = json.loads(out[out.index("{"):])
    assert code == 0
    assert len(payload["calls"]) == 2
    assert len(payload["calls"][0]) == 4
    assert len(payload["calls"][1]) == 3
    assert payload["pii_hits"] and payload["pii_hits"][0]["counts"]["email"] == 1
    assert "a@example.com" not in out
    # Nothing on disk: no .claude, no gate state, no journal.
    assert not (folder / ".claude").exists()
    assert not (tmp_path / "plugin-data" / "gate-state.json").exists()


def test_questions_hard_failure_exits_one_with_fixes(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    folder = isolate(tmp_path, monkeypatch)
    folder.mkdir()
    monkeypatch.chdir(folder)
    import preflight

    monkeypatch.setattr(
        preflight,
        "run",
        lambda backend=None: {
            "hard": [
                {
                    "name": "psql",
                    "ok": False,
                    "fix": "Install the psql client.",
                    "detail": "missing",
                }
            ],
            "soft": [],
        },
    )
    code = setup_commands.cmd_setup(Args(questions=True))
    out = capsys.readouterr().out
    assert code == 1
    assert "Install the psql client." in out
    assert not (folder / ".claude").exists()


def test_setup_router_without_mode_exits_two(tmp_path: Path, monkeypatch, capsys) -> None:
    isolate(tmp_path, monkeypatch)
    assert setup_commands.cmd_setup(Args()) == 2


def test_questions_backend_flag_preanswers(tmp_path: Path, monkeypatch, capsys) -> None:
    folder = isolate(tmp_path, monkeypatch)
    folder.mkdir()
    monkeypatch.chdir(folder)
    import preflight

    monkeypatch.setattr(
        preflight,
        "run",
        lambda backend=None: {
            "hard": [{"name": "engine root", "ok": True, "fix": "", "detail": "x"}],
            "soft": [],
        },
    )
    setup_commands.cmd_setup(Args(questions=True, backend="duckdb"))
    payload = json.loads(capsys.readouterr().out)
    backend_question = payload["calls"][0][2]
    assert backend_question["header"] == "Backend"
    assert backend_question["answered"] == "duckdb"


# ---------------------------------------------------------------------------
# snooze / decline
# ---------------------------------------------------------------------------


def test_snooze_prints_the_date(tmp_path: Path, monkeypatch, capsys) -> None:
    folder = isolate(tmp_path, monkeypatch)
    folder.mkdir()
    monkeypatch.chdir(folder)
    code = setup_controls.cmd_snooze(Args(days=3))
    out = capsys.readouterr().out
    assert code == 0
    assert "until 20" in out
    until = gate_state.folder_entry(folder)["snoozed_until"]
    assert until in out and len(until) == 10


def test_decline_sets_gate_state_and_removes_sentinel(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    folder = isolate(tmp_path, monkeypatch)
    folder.mkdir()
    setup_offer.touch_sentinel(folder)
    monkeypatch.chdir(folder)
    assert setup_controls.cmd_decline(Args()) == 0
    assert gate_state.is_declined(folder)
    assert not setup_offer.sentinel_present(folder)


# ---------------------------------------------------------------------------
# pause / resume touch only enabled
# ---------------------------------------------------------------------------


def test_pause_resume_touch_only_enabled(tmp_path: Path, monkeypatch, capsys) -> None:
    folder = isolate(tmp_path, monkeypatch)
    make_corpus_folder(folder)
    before = json.loads((folder / ".claude" / "corpus.json").read_text(encoding="utf-8"))
    stopped: list[str] = []
    monkeypatch.setattr(
        setup_controls, "_stop_study", lambda slug: stopped.append(slug) or []
    )
    monkeypatch.chdir(folder)
    assert setup_controls.cmd_pause(Args()) == 0
    assert stopped == ["q3"]
    after = json.loads((folder / ".claude" / "corpus.json").read_text(encoding="utf-8"))
    assert after == {**before, "enabled": False}
    assert setup_controls.cmd_resume(Args()) == 0
    resumed = json.loads((folder / ".claude" / "corpus.json").read_text(encoding="utf-8"))
    assert resumed == before


def test_pause_without_corpus_fails_clean(tmp_path: Path, monkeypatch, capsys) -> None:
    folder = isolate(tmp_path, monkeypatch)
    folder.mkdir()
    monkeypatch.chdir(folder)
    assert setup_controls.cmd_pause(Args()) == 1
    assert setup_controls.cmd_resume(Args()) == 1


# ---------------------------------------------------------------------------
# signals
# ---------------------------------------------------------------------------


def test_signals_prints_counts_and_touches_sentinel(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    folder = isolate(tmp_path, monkeypatch)
    folder.mkdir()
    (folder / "p1-transcript.md").write_text(
        "P1: a.\nP2: b.\nP1: c.\n", encoding="utf-8"
    )
    monkeypatch.chdir(folder)
    code = setup_controls.cmd_signals(Args())
    counts = json.loads(capsys.readouterr().out)
    assert code == 0
    assert counts["transcripts"] == 1
    assert counts["files_scanned"] == 1
    assert setup_offer.sentinel_present(folder)


# ---------------------------------------------------------------------------
# setup --off command bodies
# ---------------------------------------------------------------------------


def test_off_without_journal_or_marker_fails(tmp_path: Path, monkeypatch) -> None:
    folder = isolate(tmp_path, monkeypatch)
    folder.mkdir()
    monkeypatch.chdir(folder)
    assert setup_commands.cmd_setup(Args(off=True)) == 1


def test_off_all_without_setup_folders_is_quiet(tmp_path: Path, monkeypatch, capsys) -> None:
    isolate(tmp_path, monkeypatch)
    assert setup_commands.cmd_setup(Args(off=True, all=True)) == 0
    assert "No folder was set up" in capsys.readouterr().out
