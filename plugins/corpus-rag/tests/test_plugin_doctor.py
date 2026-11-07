"""Behavior tests for ``corpus-rag plugin-doctor`` (issue #353).

Versions, hook file state, trigger state, folder state, the last 10
hook errors for this folder, the redacted ``--bundle``, and the missing
``claude`` binary case.
"""

from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

PLUGIN = str(Path(__file__).resolve().parents[1])
if PLUGIN not in sys.path:
    sys.path.insert(0, PLUGIN)

import engine_install  # noqa: E402
import plugin_doctor  # noqa: E402
import preflight  # noqa: E402
import setup_controls  # noqa: E402


def isolate(tmp_path: Path, monkeypatch) -> Path:
    monkeypatch.setenv("CLAUDE_PLUGIN_DATA", str(tmp_path / "plugin-data"))
    monkeypatch.setenv("CORPUS_KB_STATE_DIR", str(tmp_path / "state"))
    folder = tmp_path / "folder"
    folder.mkdir()
    monkeypatch.chdir(folder)
    return folder


class Args:
    def __init__(self, bundle: bool = False):
        self.bundle = bundle


def test_report_has_versions_and_states(tmp_path: Path, monkeypatch) -> None:
    folder = isolate(tmp_path, monkeypatch)
    data = plugin_doctor.report(folder)
    assert data["versions"]["plugin"] == engine_install.plugin_version()
    assert data["versions"]["python"]
    assert data["versions"]["claude"] == "unknown"  # no claude binary in tests
    assert data["hooks"]["ok"] is True
    assert data["triggers"]["ok"] is True
    assert data["triggers"]["nouns"] >= 1
    assert data["folder"]["state"] in ("corpus", "research", "off", "none")


def test_hook_file_state_lists_missing_files(tmp_path: Path, monkeypatch) -> None:
    isolate(tmp_path, monkeypatch)
    copy = tmp_path / "plugin-copy"
    shutil.copytree(plugin_doctor.PLUGIN_ROOT, copy, ignore=shutil.ignore_patterns("__pycache__"))
    (copy / "hooks" / "file_changed.py").unlink()
    monkeypatch.setattr(plugin_doctor, "PLUGIN_ROOT", copy)
    state = plugin_doctor.hook_file_state()
    assert state["ok"] is False
    assert state["missing"] == ["hooks/file_changed.py"]


def test_exit_zero_only_when_hooks_and_triggers_good(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    isolate(tmp_path, monkeypatch)
    assert setup_controls.cmd_plugin_doctor(Args()) == 0
    copy = tmp_path / "plugin-copy"
    shutil.copytree(plugin_doctor.PLUGIN_ROOT, copy, ignore=shutil.ignore_patterns("__pycache__"))
    (copy / "hooks" / "triggers.json").unlink()
    monkeypatch.setattr(plugin_doctor, "PLUGIN_ROOT", copy)
    assert setup_controls.cmd_plugin_doctor(Args()) == 1
    out = capsys.readouterr().out
    assert "triggers: not ok" in out


def test_missing_claude_binary_reports_unknown(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    isolate(tmp_path, monkeypatch)
    monkeypatch.setattr(plugin_doctor.shutil, "which", lambda name: None)
    setup_controls.cmd_plugin_doctor(Args())
    assert "claude: unknown" in capsys.readouterr().out


def test_claude_version_parses_binary_output(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(plugin_doctor.shutil, "which", lambda name: "/fake/claude")

    def fake_run(command, capture_output, text, timeout, check):
        return type("R", (), {"returncode": 0, "stdout": "2.1.34 (Claude Code)", "stderr": ""})()

    monkeypatch.setattr(plugin_doctor.subprocess, "run", fake_run)
    assert plugin_doctor.claude_version() == "2.1.34"


def test_hook_errors_for_this_folder(tmp_path: Path, monkeypatch) -> None:
    folder = isolate(tmp_path, monkeypatch)
    log = tmp_path / "plugin-data" / "hook-errors.jsonl"
    log.parent.mkdir(parents=True, exist_ok=True)
    rows = [
        {"ts": 1, "where": "prompt_submit", "detail": f"boom at {folder}\\file"},
        {"ts": 2, "where": "stop", "detail": "somewhere else"},
        {"ts": 3, "where": "stop", "detail": f"again at {folder}"},
    ]
    log.write_text(
        "\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8"
    )
    errors = plugin_doctor.hook_errors_for(folder)
    assert len(errors) == 2
    assert errors[0]["where"] == "prompt_submit"


def test_redact_replaces_the_folder_before_home(tmp_path: Path, monkeypatch) -> None:
    isolate(tmp_path, monkeypatch)
    folder = Path.cwd()  # under the home folder on CI and dev machines
    out = plugin_doctor.redact(f"logged in {folder}", folder)
    assert out == "logged in <folder>"
    assert "~" not in out


def test_bundle_is_redacted(tmp_path: Path, monkeypatch, capsys) -> None:
    isolate(tmp_path, monkeypatch)
    folder = Path.cwd()
    log = tmp_path / "plugin-data" / "hook-errors.jsonl"
    log.parent.mkdir(parents=True, exist_ok=True)
    log.write_text(
        json.dumps(
            {
                "ts": 1,
                "where": "stop",
                "detail": (
                    "postgres://user:sekret@localhost/db at "
                    f"{Path.home()} mail me@example.com from {folder}"
                ),
            }
        )
        + "\n",
        encoding="utf-8",
    )
    assert setup_controls.cmd_plugin_doctor(Args(bundle=True)) == 0
    out = capsys.readouterr().out
    assert "Bundle written" in out
    bundle = json.loads(plugin_doctor.bundle_path().read_text(encoding="utf-8"))
    blob = json.dumps(bundle)
    assert "sekret" not in blob
    assert "me@example.com" not in blob
    assert str(Path.home()) not in blob
    assert str(folder) not in blob
    assert "<folder>" in blob
    detail = bundle["hook_errors"][0]["detail"]  # the path here sits under home
    assert "<folder>" in detail
    assert detail.endswith("from <folder>")


def test_engine_report_carries_the_minimum(tmp_path: Path, monkeypatch) -> None:
    isolate(tmp_path, monkeypatch)
    monkeypatch.setattr(plugin_doctor.engine_paths, "engine_root", lambda root=None: None)
    report = plugin_doctor.engine_report()
    assert report["root"] is None
    assert report["minimum"] == preflight.MINIMUM_ENGINE_VERSION
    assert report["ok"] is False
