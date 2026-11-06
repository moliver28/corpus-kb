"""Behavior tests for the corpus-rag nudge hooks.

Each test runs the real script through ``sh`` with an isolated state dir
(``CORPUS_KB_PLUGIN_STATE_DIR``). The owner's real ``~/.corpus-kb`` is never
touched. Tests skip when ``sh`` is missing (engine CI runs them on ubuntu).
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

HOOKS = Path(__file__).resolve().parents[1] / "hooks"
SH = shutil.which("sh")

pytestmark = pytest.mark.skipif(SH is None, reason="sh not available")


def run_hook(script: str, stdin_obj: dict[str, str], state: Path) -> str:
    result = subprocess.run(
        [str(SH), str(HOOKS / script)],
        input=json.dumps(stdin_obj),
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
        env={"PATH": "/usr/bin:/bin", "CORPUS_KB_PLUGIN_STATE_DIR": str(state), "HOME": str(state)},
    )
    assert result.returncode == 0, result.stdout + result.stderr
    return result.stdout


def fresh_state(tmp_path: Path) -> Path:
    state = tmp_path / "plugin-state"
    state.mkdir(parents=True)
    return state


def register(state: Path) -> None:
    (state / "study-registered").write_text("", encoding="utf-8")


# ---------------------------------------------------------------------------
# hooks.json structure
# ---------------------------------------------------------------------------


def test_hooks_json_structure() -> None:
    import json

    data = json.loads((HOOKS / "hooks.json").read_text(encoding="utf-8"))
    supported = {
        "SessionStart",
        "UserPromptSubmit",
        "PreToolUse",
        "PermissionRequest",
        "PostToolUse",
        "PostToolUseFailure",
        "Stop",
    }
    unsupported = {"PreCompact", "SubagentStop", "Notification"}
    hooks = data["hooks"]
    assert set(hooks) <= supported
    assert not (set(hooks) & unsupported)
    for event, entries in hooks.items():
        for entry in entries:
            for hook in entry["hooks"]:
                assert hook["type"] == "command"
                assert '[ -f "$f" ] || exit 0' in hook["command"]


# ---------------------------------------------------------------------------
# session-nudge.sh
# ---------------------------------------------------------------------------


def test_session_silent_outside_research(tmp_path: Path) -> None:
    state = fresh_state(tmp_path)
    out = run_hook("session-nudge.sh", {"cwd": "/home/u/plain-project"}, state)
    assert out.strip() == ""


def test_session_nudges_inside_research(tmp_path: Path) -> None:
    state = fresh_state(tmp_path)
    out = run_hook("session-nudge.sh", {"cwd": "/home/u/Research/q3-talks"}, state)
    assert "corpus-kb ingest" in out
    marks = list((state / "marks").glob("session-*"))
    assert len(marks) == 1


def test_session_rate_limited_same_day(tmp_path: Path) -> None:
    state = fresh_state(tmp_path)
    first = run_hook("session-nudge.sh", {"cwd": "/home/u/Research/q3"}, state)
    second = run_hook("session-nudge.sh", {"cwd": "/home/u/Research/q3"}, state)
    assert "corpus-kb ingest" in first
    assert second.strip() == ""


def test_session_silent_when_registered(tmp_path: Path) -> None:
    state = fresh_state(tmp_path)
    register(state)
    out = run_hook("session-nudge.sh", {"cwd": "/home/u/Research/q3"}, state)
    assert out.strip() == ""


def test_session_silent_when_declined(tmp_path: Path) -> None:
    state = fresh_state(tmp_path)
    (state / "marks" / "declined-session").parent.mkdir(parents=True)
    (state / "marks" / "declined-session").write_text("", encoding="utf-8")
    out = run_hook("session-nudge.sh", {"cwd": "/home/u/Research/q3"}, state)
    assert out.strip() == ""


def test_session_handles_windows_cwd(tmp_path: Path) -> None:
    state = fresh_state(tmp_path)
    out = run_hook("session-nudge.sh", {"cwd": "F:\\Users\\u\\Research\\q3"}, state)
    assert "corpus-kb ingest" in out


def test_session_silent_on_broken_json(tmp_path: Path) -> None:
    state = fresh_state(tmp_path)
    result = subprocess.run(
        [str(SH), str(HOOKS / "session-nudge.sh")],
        input="this is not json",
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
        env={"PATH": "/usr/bin:/bin", "CORPUS_KB_PLUGIN_STATE_DIR": str(state), "HOME": str(state)},
    )
    assert result.returncode == 0
    assert result.stdout.strip() == ""


# ---------------------------------------------------------------------------
# prompt-nudge.sh
# ---------------------------------------------------------------------------


def test_prompt_silent_without_study(tmp_path: Path) -> None:
    state = fresh_state(tmp_path)
    out = run_hook("prompt-nudge.sh", {"prompt": "what did they say in the interview?"}, state)
    assert out.strip() == ""


def test_prompt_ask_lane(tmp_path: Path) -> None:
    state = fresh_state(tmp_path)
    register(state)
    out = run_hook("prompt-nudge.sh", {"prompt": "what themes appear in the interviews?"}, state)
    assert "corpus-kb ask" in out
    assert any(m.name.startswith("prompt-ask-") for m in (state / "marks").iterdir())


def test_prompt_ask_lane_rate_limited(tmp_path: Path) -> None:
    state = fresh_state(tmp_path)
    register(state)
    run_hook("prompt-nudge.sh", {"prompt": "what themes appear?"}, state)
    out = run_hook("prompt-nudge.sh", {"prompt": "why did they decline?"}, state)
    assert out.strip() == ""


def test_prompt_analyze_lane(tmp_path: Path) -> None:
    state = fresh_state(tmp_path)
    register(state)
    out = run_hook(
        "prompt-nudge.sh",
        {"prompt": "which test should I run on these groups?"},
        state,
    )
    assert "analysis-select-method" in out


def test_prompt_ingest_lane(tmp_path: Path) -> None:
    state = fresh_state(tmp_path)
    register(state)
    out = run_hook(
        "prompt-nudge.sh",
        {"prompt": "please ingest the file notes.pdf"},
        state,
    )
    assert "corpus-kb ingest" in out


def test_prompt_declined_lane_silent(tmp_path: Path) -> None:
    state = fresh_state(tmp_path)
    register(state)
    (state / "marks").mkdir()
    (state / "marks" / "declined-ask").write_text("", encoding="utf-8")
    out = run_hook("prompt-nudge.sh", {"prompt": "what happened in the meeting?"}, state)
    assert out.strip() == ""


def test_prompt_silent_on_broken_json(tmp_path: Path) -> None:
    state = fresh_state(tmp_path)
    result = subprocess.run(
        [str(SH), str(HOOKS / "prompt-nudge.sh")],
        input="not json at all",
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
        env={"PATH": "/usr/bin:/bin", "CORPUS_KB_PLUGIN_STATE_DIR": str(state), "HOME": str(state)},
    )
    assert result.returncode == 0
    assert result.stdout.strip() == ""
