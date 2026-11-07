"""Behavior tests for the seven study-folder hooks (issue #352).

Two layers:

- Python level: the ``run_*`` functions in ``hook_events`` and
  ``hook_watch``, with the count query replaced, so no test launches a
  real engine server.
- Shell level: ``hooks/dispatch.sh`` with the real guard, run through
  ``sh``. A fake interpreter marks whether Python started, which pins
  "no Python start outside a study folder" and the five silenced events.

The old Research/<project> lane hooks are gone; folder state decides.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

PLUGIN = str(Path(__file__).resolve().parents[1])
if PLUGIN not in sys.path:
    sys.path.insert(0, PLUGIN)

import hook_events  # noqa: E402
import hook_session  # noqa: E402
import hook_state  # noqa: E402
import hook_turn  # noqa: E402
import hook_watch  # noqa: E402
import ingest_queue  # noqa: E402

HOOKS = Path(PLUGIN) / "hooks"
SH = shutil.which("sh")

pytestmark_sh = pytest.mark.skipif(SH is None, reason="sh not available")


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------


def setup_data(tmp_path: Path, monkeypatch) -> Path:
    data = tmp_path / "plugin-data"
    monkeypatch.setenv("CLAUDE_PLUGIN_DATA", str(data))
    return data


def corpus_root(tmp_path: Path, monkeypatch, slug: str = "q3", **keys) -> Path:
    """A corpus-state folder with a registered study."""
    data = setup_data(tmp_path, monkeypatch)
    root = tmp_path / "study"
    (root / ".claude").mkdir(parents=True)
    marker = {"slug": slug, "watch": ["interviews"]}
    marker.update(keys)
    (root / ".claude" / "corpus.json").write_text(json.dumps(marker), encoding="utf-8")
    (data / "studies.json").parent.mkdir(parents=True, exist_ok=True)
    (data / "studies.json").write_text(
        json.dumps(
            {
                "active": None,
                "studies": {slug: {"database_url": "x", "port": 8010, "backend": "postgres"}},
            }
        ),
        encoding="utf-8",
    )
    (root / "interviews").mkdir()
    return root


def counts_stub(monkeypatch, documents=12, last="2026-11-05T10:00:00", down=False):
    if down:
        monkeypatch.setattr(hook_session, "fetch_counts", lambda study: None)
    else:
        monkeypatch.setattr(
            hook_session,
            "fetch_counts",
            lambda study: {"documents": documents, "last_ingest": last},
        )


# ---------------------------------------------------------------------------
# SessionStart: exact block, watchPaths, silence, dedup, prune
# ---------------------------------------------------------------------------


def test_session_start_block_is_exact(tmp_path: Path, monkeypatch) -> None:
    root = corpus_root(tmp_path, monkeypatch)
    counts_stub(monkeypatch)
    out = hook_session.run_session_start({"cwd": str(root), "session_id": "s1"})
    payload = json.loads(out)
    block = payload["hookSpecificOutput"]["additionalContext"]
    assert block == (
        "Study: q3\n"
        "Documents: 12 (last ingest 2026-11-05)\n"
        "Search the corpus before reading transcripts by hand.\n"
        "Cite every claim as [S:<source-slug>].\n"
        "analysis/, deliverables/, and *-summary.md are not evidence.\n"
        "Skills: /corpus-ask, /corpus-analyze, /corpus-ingest, /corpus-admin"
    )


def test_session_start_watchpaths_are_the_watch_folders(tmp_path: Path, monkeypatch) -> None:
    root = corpus_root(tmp_path, monkeypatch)
    counts_stub(monkeypatch)
    payload = json.loads(hook_session.run_session_start({"cwd": str(root)}))
    assert payload["watchPaths"] == [str(root / "interviews")]


def test_session_start_engine_down_line(tmp_path: Path, monkeypatch) -> None:
    root = corpus_root(tmp_path, monkeypatch)
    counts_stub(monkeypatch, down=True)
    payload = json.loads(hook_session.run_session_start({"cwd": str(root)}))
    block = payload["hookSpecificOutput"]["additionalContext"]
    assert "Engine down. Run `corpus-rag status`." in block
    assert "Study: q3" in block


def test_session_start_silent_without_corpus(tmp_path: Path, monkeypatch) -> None:
    setup_data(tmp_path, monkeypatch)
    plain = tmp_path / "plain"
    plain.mkdir()
    assert hook_session.run_session_start({"cwd": str(plain)}) is None


def test_session_start_silent_when_declined(tmp_path: Path, monkeypatch) -> None:
    root = corpus_root(tmp_path, monkeypatch)
    hook_state.touch_mark(hook_events.declined_mark_for(root))
    assert hook_session.run_session_start({"cwd": str(root)}) is None


def test_session_start_silent_when_unregistered(tmp_path: Path, monkeypatch) -> None:
    root = corpus_root(tmp_path, monkeypatch)
    (Path(os.environ["CLAUDE_PLUGIN_DATA"]) / "studies.json").write_text(
        json.dumps({"active": None, "studies": {}}), encoding="utf-8"
    )
    assert hook_session.run_session_start({"cwd": str(root)}) is None


def test_session_start_silent_when_enabled_false(tmp_path: Path, monkeypatch) -> None:
    root = corpus_root(tmp_path, monkeypatch, enabled=False)
    assert hook_session.run_session_start({"cwd": str(root)}) is None


def test_session_start_dedup_same_event_within_5s(tmp_path: Path, monkeypatch) -> None:
    root = corpus_root(tmp_path, monkeypatch)
    counts_stub(monkeypatch)
    first = hook_session.run_session_start({"cwd": str(root), "hook_event_name": "startup"})
    second = hook_session.run_session_start({"cwd": str(root), "hook_event_name": "startup"})
    assert first is not None
    assert second is None


def test_session_start_dedup_is_per_event(tmp_path: Path, monkeypatch) -> None:
    root = corpus_root(tmp_path, monkeypatch)
    counts_stub(monkeypatch)
    assert hook_session.run_session_start({"cwd": str(root), "hook_event_name": "startup"})
    assert hook_session.run_session_start({"cwd": str(root), "hook_event_name": "compact"})


def test_session_start_research_offer_once_per_day(tmp_path: Path, monkeypatch) -> None:
    setup_data(tmp_path, monkeypatch)
    folder = tmp_path / "plain"
    (folder / "interviews").mkdir(parents=True)
    (folder / "interviews" / "p1.md").write_text(
        "P1: Hello.\nP2: Hi.\nP1: Next.\nP2: Go.\n", encoding="utf-8"
    )
    first = hook_session.run_session_start({"cwd": str(folder)})
    second = hook_session.run_session_start({"cwd": str(folder)})
    assert first is not None and "corpus" in first.lower()
    assert second is None


def test_session_start_prunes_state_once_a_day(tmp_path: Path, monkeypatch) -> None:
    setup_data(tmp_path, monkeypatch)
    root = corpus_root(tmp_path, monkeypatch)
    counts_stub(monkeypatch)
    hook_session.run_session_start({"cwd": str(root)})
    assert hook_state.mark_exists(".pruned-marker") or (
        Path(os.environ["CLAUDE_PLUGIN_DATA"]) / ".pruned"
    ).is_file()


# ---------------------------------------------------------------------------
# UserPromptSubmit: scan-queue + lane nudges keyed per study
# ---------------------------------------------------------------------------


def test_prompt_submit_queues_new_files(tmp_path: Path, monkeypatch) -> None:
    root = corpus_root(tmp_path, monkeypatch)
    (root / "interviews" / "p1.md").write_text(
        "P1: Hello.\nP2: Hi.\nP1: Next.\nP2: Go.\n", encoding="utf-8"
    )
    out = hook_events.run_prompt_submit({"cwd": str(root), "prompt": "hello", "session_id": "s1"})
    rows = ingest_queue.queue_rows(root)
    assert len(rows) == 1
    assert rows[0]["kind"] == "transcript"
    assert out is None or "ask" not in out.lower()


def test_prompt_submit_scan_is_capped(tmp_path: Path, monkeypatch) -> None:
    root = corpus_root(tmp_path, monkeypatch)
    for i in range(320):
        (root / "interviews" / f"f{i:03}.txt").write_text("x", encoding="utf-8")
    hook_events.run_prompt_submit({"cwd": str(root), "prompt": "hello"})
    assert len(ingest_queue.queue_rows(root)) == 300


def test_prompt_submit_rescan_only_queues_changes(tmp_path: Path, monkeypatch) -> None:
    root = corpus_root(tmp_path, monkeypatch)
    (root / "interviews" / "a.md").write_text("x", encoding="utf-8")
    hook_events.run_prompt_submit({"cwd": str(root), "prompt": "hello"})
    hook_events.run_prompt_submit({"cwd": str(root), "prompt": "hello again"})
    assert len(ingest_queue.queue_rows(root)) == 1
    (root / "interviews" / "b.md").write_text("y", encoding="utf-8")
    hook_events.run_prompt_submit({"cwd": str(root), "prompt": "third"})
    assert len(ingest_queue.queue_rows(root)) == 2


def test_prompt_ask_lane_keyed_per_study(tmp_path: Path, monkeypatch) -> None:
    root = corpus_root(tmp_path, monkeypatch)
    out = hook_events.run_prompt_submit({"cwd": str(root), "prompt": "what did they say?"})
    assert "corpus-rag ask" in out
    assert "-p q3" in out
    marks = list((Path(os.environ["CLAUDE_PLUGIN_DATA"]) / "marks").glob("prompt-q3-ask-*"))
    assert len(marks) == 1


def test_prompt_lane_rate_limited_per_study_per_day(tmp_path: Path, monkeypatch) -> None:
    root = corpus_root(tmp_path, monkeypatch)
    first = hook_events.run_prompt_submit({"cwd": str(root), "prompt": "what did they say?"})
    second = hook_events.run_prompt_submit({"cwd": str(root), "prompt": "why did they stop?"})
    assert first and not second


def test_prompt_declined_lane_silent(tmp_path: Path, monkeypatch) -> None:
    root = corpus_root(tmp_path, monkeypatch)
    hook_state.touch_mark("declined-ask")
    assert hook_events.run_prompt_submit({"cwd": str(root), "prompt": "what happened?"}) is None


def test_prompt_analyze_lane(tmp_path: Path, monkeypatch) -> None:
    root = corpus_root(tmp_path, monkeypatch)
    out = hook_events.run_prompt_submit({"cwd": str(root), "prompt": "which test should I run?"})
    assert "analysis-select-method" in out


def test_prompt_silent_outside_corpus(tmp_path: Path, monkeypatch) -> None:
    setup_data(tmp_path, monkeypatch)
    plain = tmp_path / "plain"
    plain.mkdir()
    assert hook_events.run_prompt_submit({"cwd": str(plain), "prompt": "what?"}) is None


def test_prompt_submit_starts_a_new_turn(tmp_path: Path, monkeypatch) -> None:
    root = corpus_root(tmp_path, monkeypatch)
    hook_state.save_session("s1", dict(hook_state.fresh_session("q3", root), turn=5))
    hook_events.run_prompt_submit({"cwd": str(root), "prompt": "x", "session_id": "s1"})
    session = hook_state.load_session("s1")
    assert session["turn"] == 6
    assert session["turn_reads"] == []


# ---------------------------------------------------------------------------
# SubagentStart
# ---------------------------------------------------------------------------


def test_subagent_start_three_lines(tmp_path: Path, monkeypatch) -> None:
    root = corpus_root(tmp_path, monkeypatch)
    out = hook_events.run_subagent_start({"cwd": str(root), "session_id": "s1"})
    lines = out.strip().splitlines()
    assert len(lines) == 3
    assert "corpus-rag search" in lines[0]
    assert "q3" in lines[1]
    assert "[S:" in lines[2]


def test_subagent_start_marks_the_session(tmp_path: Path, monkeypatch) -> None:
    root = corpus_root(tmp_path, monkeypatch)
    hook_events.run_subagent_start({"cwd": str(root), "session_id": "s1"})
    assert hook_state.load_session("s1")["subagent"] is True


def test_subagent_start_silent_outside_corpus(tmp_path: Path, monkeypatch) -> None:
    setup_data(tmp_path, monkeypatch)
    plain = tmp_path / "plain"
    plain.mkdir()
    assert hook_events.run_subagent_start({"cwd": str(plain)}) is None


# ---------------------------------------------------------------------------
# PreToolUse: read nudge
# ---------------------------------------------------------------------------


def make_transcript(folder: Path, name: str = "p1.md") -> Path:
    path = folder / name
    path.write_text("P1: Hello.\nP2: Hi.\nP1: Next.\nP2: Go.\n", encoding="utf-8")
    return path


def nudged_session(tmp_path: Path, monkeypatch, root: Path) -> None:
    hook_state.save_session("s1", hook_state.fresh_session("q3", root))


def test_pre_tool_use_nudges_on_read(tmp_path: Path, monkeypatch) -> None:
    root = corpus_root(tmp_path, monkeypatch)
    file = make_transcript(root / "interviews")
    nudged_session(tmp_path, monkeypatch, root)
    data = {
        "cwd": str(root),
        "session_id": "s1",
        "tool_name": "Read",
        "tool_input": {"file_path": str(file)},
    }
    out = hook_watch.run_pre_tool_use(data)
    assert "corpus-rag search" in out
    assert hook_state.load_session("s1")["turn_reads"] == [str(file)]


def test_pre_tool_use_nudge_cap_three(tmp_path: Path, monkeypatch) -> None:
    root = corpus_root(tmp_path, monkeypatch)
    file = make_transcript(root / "interviews")
    nudged_session(tmp_path, monkeypatch, root)
    data = {
        "cwd": str(root),
        "session_id": "s1",
        "tool_name": "Read",
        "tool_input": {"file_path": str(file)},
    }
    outs = [hook_watch.run_pre_tool_use(dict(data)) for _ in range(4)]
    assert sum(1 for out in outs if out) == 3


def test_pre_tool_use_skips_subagent_calls(tmp_path: Path, monkeypatch) -> None:
    root = corpus_root(tmp_path, monkeypatch)
    file = make_transcript(root / "interviews")
    hook_state.save_session(
        "s1", dict(hook_state.fresh_session("q3", root), subagent=True)
    )
    data = {
        "cwd": str(root),
        "session_id": "s1",
        "tool_name": "Read",
        "tool_input": {"file_path": str(file)},
    }
    assert hook_watch.run_pre_tool_use(data) is None


def test_pre_tool_use_no_nudge_after_corpus_call_same_turn(tmp_path: Path, monkeypatch) -> None:
    root = corpus_root(tmp_path, monkeypatch)
    file = make_transcript(root / "interviews")
    nudged_session(tmp_path, monkeypatch, root)
    bash = {
        "cwd": str(root),
        "session_id": "s1",
        "tool_name": "Bash",
        "tool_input": {"command": "corpus-rag search 'themes' -p q3"},
    }
    assert hook_watch.run_pre_tool_use(bash) is None  # a corpus call, not a read
    read = {
        "cwd": str(root),
        "session_id": "s1",
        "tool_name": "Read",
        "tool_input": {"file_path": str(file)},
    }
    assert hook_watch.run_pre_tool_use(read) is None  # already called the corpus
    session = hook_state.load_session("s1")
    assert session["corpus_called_at"] is not None
    assert session["turn_reads"] == [str(file)]  # read still noted for telemetry


def test_pre_tool_use_detects_bash_readers(tmp_path: Path, monkeypatch) -> None:
    root = corpus_root(tmp_path, monkeypatch)
    file = make_transcript(root / "interviews")
    nudged_session(tmp_path, monkeypatch, root)
    for command in (
        f"cat '{file}'",
        f"grep -n 'theme' {file}",
        f"rg theme {file}",
        f"sed -n '1,20p' {file}",
    ):
        hook_state.save_session("s1", hook_state.fresh_session("q3", root))
        data = {
            "cwd": str(root),
            "session_id": "s1",
            "tool_name": "Bash",
            "tool_input": {"command": command},
        }
        out = hook_watch.run_pre_tool_use(data)
        assert out is not None, command


def test_pre_tool_use_ignores_plain_bash_and_other_files(tmp_path: Path, monkeypatch) -> None:
    root = corpus_root(tmp_path, monkeypatch)
    notes = root / "interviews" / "notes.md"
    notes.write_text("plain notes", encoding="utf-8")
    nudged_session(tmp_path, monkeypatch, root)
    for tool_name, tool_input in (
        ("Bash", {"command": "echo hello"}),
        ("Read", {"file_path": str(notes)}),
        ("Read", {"file_path": str(root / "elsewhere" / "p1.md")}),
    ):
        data = {
            "cwd": str(root),
            "session_id": "s1",
            "tool_name": tool_name,
            "tool_input": tool_input,
        }
        assert hook_watch.run_pre_tool_use(data) is None, (tool_name, tool_input)


def test_pre_tool_use_silent_without_session(tmp_path: Path, monkeypatch) -> None:
    root = corpus_root(tmp_path, monkeypatch)
    file = make_transcript(root / "interviews")
    data = {
        "cwd": str(root),
        "session_id": "ghost",
        "tool_name": "Read",
        "tool_input": {"file_path": str(file)},
    }
    assert hook_watch.run_pre_tool_use(data) is None


def test_pre_tool_use_respects_excludes(tmp_path: Path, monkeypatch) -> None:
    root = corpus_root(tmp_path, monkeypatch, exclude=["interviews/**"])
    file = make_transcript(root / "interviews")
    nudged_session(tmp_path, monkeypatch, root)
    data = {
        "cwd": str(root),
        "session_id": "s1",
        "tool_name": "Read",
        "tool_input": {"file_path": str(file)},
    }
    assert hook_watch.run_pre_tool_use(data) is None


# ---------------------------------------------------------------------------
# PostToolUse and FileChanged: queue writes
# ---------------------------------------------------------------------------


def queued_names(root: Path) -> list[str]:
    return [Path(row["path"]).name for row in ingest_queue.queue_rows(root)]


def test_post_tool_use_queues_written_file(tmp_path: Path, monkeypatch) -> None:
    root = corpus_root(tmp_path, monkeypatch)
    file = make_transcript(root / "interviews")
    data = {
        "cwd": str(root),
        "tool_name": "Write",
        "tool_input": {"file_path": str(file)},
    }
    hook_turn.run_post_tool_use(data)
    assert queued_names(root) == [file.name]


def test_post_tool_use_skips_junk_paths(tmp_path: Path, monkeypatch) -> None:
    root = corpus_root(tmp_path, monkeypatch)
    junk = [
        root / "interviews" / ".hidden.md",
        root / "interviews" / "notes.tmp",
        root / "interviews" / "notes.swp",
        root / "interviews" / "notes.md~",
        root / ".claude" / "corpus.json",
    ]
    for path in junk:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("P1: a\nP2: b\nP1: c\nP2: d\n", encoding="utf-8")
    for path in junk:
        data = {"cwd": str(root), "tool_name": "Edit", "tool_input": {"file_path": str(path)}}
        hook_turn.run_post_tool_use(data)
    assert queued_names(root) == []


def test_post_tool_use_skips_excluded_paths(tmp_path: Path, monkeypatch) -> None:
    root = corpus_root(tmp_path, monkeypatch)
    file = root / "analysis" / "themes.md"
    file.parent.mkdir(parents=True)
    file.write_text("notes", encoding="utf-8")
    data = {"cwd": str(root), "tool_name": "Write", "tool_input": {"file_path": str(file)}}
    hook_turn.run_post_tool_use(data)
    assert ingest_queue.queue_rows(root) == []


def test_file_changed_queues_and_ignores_unlink(tmp_path: Path, monkeypatch) -> None:
    root = corpus_root(tmp_path, monkeypatch)
    file = make_transcript(root / "interviews")
    hook_turn.run_file_changed({"cwd": str(root), "path": str(file), "kind": "change"})
    assert queued_names(root) == [file.name]
    hook_turn.run_file_changed({"cwd": str(root), "path": str(file), "kind": "unlink"})
    assert queued_names(root) == [file.name]


def test_file_changed_ignores_missing_file(tmp_path: Path, monkeypatch) -> None:
    root = corpus_root(tmp_path, monkeypatch)
    hook_turn.run_file_changed({"cwd": str(root), "path": str(root / "ghost.md")})
    assert ingest_queue.queue_rows(root) == []


def test_queue_never_queues_itself(tmp_path: Path, monkeypatch) -> None:
    root = corpus_root(tmp_path, monkeypatch)
    queue_file = root / ".claude" / "corpus-pending.jsonl"
    queue_file.write_text("", encoding="utf-8")
    hook_turn.run_file_changed({"cwd": str(root), "path": str(queue_file)})
    assert ingest_queue.queue_rows(root) == []


# ---------------------------------------------------------------------------
# Stop: telemetry
# ---------------------------------------------------------------------------


def test_stop_logs_manual_read_before_corpus_call(tmp_path: Path, monkeypatch) -> None:
    root = corpus_root(tmp_path, monkeypatch)
    file = make_transcript(root / "interviews")
    session = hook_state.fresh_session("q3", root)
    session["turn_reads"] = [str(file)]
    session["first_read_at"] = 100.0
    hook_state.save_session("s1", session)
    assert hook_turn.run_stop({"cwd": str(root), "session_id": "s1"}) is None
    log = Path(os.environ["CLAUDE_PLUGIN_DATA"]) / "telemetry.jsonl"
    rows = [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]
    assert len(rows) == 1
    assert rows[0]["kind"] == "manual_read"
    assert rows[0]["paths"] == [str(file)]


def test_stop_silent_when_corpus_call_came_first(tmp_path: Path, monkeypatch) -> None:
    root = corpus_root(tmp_path, monkeypatch)
    session = hook_state.fresh_session("q3", root)
    session["turn_reads"] = [str(root / "interviews" / "p1.md")]
    session["first_read_at"] = 100.0
    session["corpus_called_at"] = 90.0  # corpus call happened before the read
    hook_state.save_session("s1", session)
    hook_turn.run_stop({"cwd": str(root), "session_id": "s1"})
    log = Path(os.environ["CLAUDE_PLUGIN_DATA"]) / "telemetry.jsonl"
    assert not log.exists()


def test_stop_resets_the_turn_and_subagent(tmp_path: Path, monkeypatch) -> None:
    root = corpus_root(tmp_path, monkeypatch)
    session = hook_state.fresh_session("q3", root)
    session.update({"turn_reads": ["x"], "first_read_at": 1.0, "subagent": True})
    hook_state.save_session("s1", session)
    hook_turn.run_stop({"cwd": str(root), "session_id": "s1"})
    after = hook_state.load_session("s1")
    assert after["turn_reads"] == []
    assert after["first_read_at"] is None
    assert after["subagent"] is False


def test_stop_is_cheap_without_a_session(tmp_path: Path, monkeypatch) -> None:
    setup_data(tmp_path, monkeypatch)
    assert hook_turn.run_stop({"cwd": str(tmp_path), "session_id": "ghost"}) is None


# ---------------------------------------------------------------------------
# Shell level: dispatch.sh, guard, hooks.json
# ---------------------------------------------------------------------------


def run_dispatch(name: str, stdin_obj: dict, tmp_path: Path, fake_python: Path | None, cwd: Path):
    env = {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "CLAUDE_PLUGIN_DATA": str(tmp_path / "plugin-data"),
        "HOME": str(tmp_path),
    }
    if fake_python is not None:
        env["CORPUS_KB_HOOK_PYTHON"] = str(fake_python)
    return subprocess.run(
        [str(SH), str(HOOKS / "dispatch.sh"), name],
        input=json.dumps(stdin_obj),
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
        env=env,
        cwd=str(cwd),
    )


def write_fake_python(tmp_path: Path) -> Path:
    """An interpreter stand-in that proves Python started (marker + exit 0)."""
    fake = tmp_path / "fake-python"
    fake.write_text(
        "#!/bin/sh\nprintf started >> \"$FAKE_PYTHON_MARKER\"\nexit 0\n", encoding="utf-8"
    )
    fake.chmod(0o755)
    return fake


@pytest.mark.skipif(SH is None, reason="sh not available")
def test_hooks_json_wires_all_seven_events() -> None:
    data = json.loads((HOOKS / "hooks.json").read_text(encoding="utf-8"))
    hooks = data["hooks"]
    assert set(hooks) == {
        "SessionStart",
        "UserPromptSubmit",
        "SubagentStart",
        "PreToolUse",
        "PostToolUse",
        "FileChanged",
        "Stop",
    }
    matchers = {
        "SessionStart": "startup|compact|clear",
        "PreToolUse": "Read|Grep|Bash",
        "PostToolUse": "Write|Edit",
    }
    for event, entries in hooks.items():
        for entry in entries:
            if event in matchers:
                assert entry["matcher"] == matchers[event]
            for hook in entry["hooks"]:
                assert hook["type"] == "command"
                assert '[ -f "$f" ] || exit 0' in hook["command"]
                assert "dispatch.sh" in hook["command"]


@pytest.mark.skipif(SH is None, reason="sh not available")
def test_guard_no_python_outside_study_folder(tmp_path: Path) -> None:
    fake = write_fake_python(tmp_path)
    plain = tmp_path / "plain"
    plain.mkdir()
    marker = tmp_path / "marker"
    env_marker = marker
    result = subprocess.run(
        [str(SH), str(HOOKS / "dispatch.sh"), "pre_tool_use"],
        input=json.dumps({"cwd": str(plain)}),
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
        env={
            "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
            "CLAUDE_PLUGIN_DATA": str(tmp_path / "plugin-data"),
            "CORPUS_KB_HOOK_PYTHON": str(fake),
            "FAKE_PYTHON_MARKER": str(env_marker),
        },
        cwd=str(plain),
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert not marker.exists()


@pytest.mark.skipif(SH is None, reason="sh not available")
@pytest.mark.parametrize(
    "name",
    ["prompt_submit", "subagent_start", "pre_tool_use", "post_tool_use", "file_changed"],
)
def test_guard_enabled_false_silences_five_events(tmp_path: Path, name: str) -> None:
    fake = write_fake_python(tmp_path)
    root = tmp_path / "study"
    (root / ".claude").mkdir(parents=True)
    (root / ".claude" / "corpus.json").write_text(
        '{"slug": "q3", "enabled": false}', encoding="utf-8"
    )
    marker = tmp_path / f"marker-{name}"
    result = subprocess.run(
        [str(SH), str(HOOKS / "dispatch.sh"), name],
        input=json.dumps({"cwd": str(root)}),
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
        env={
            "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
            "CLAUDE_PLUGIN_DATA": str(tmp_path / "plugin-data"),
            "CORPUS_KB_HOOK_PYTHON": str(fake),
            "FAKE_PYTHON_MARKER": str(marker),
        },
        cwd=str(root),
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert not marker.exists()


@pytest.mark.skipif(SH is None, reason="sh not available")
def test_guard_missing_body_script_exits_zero(tmp_path: Path) -> None:
    plain = tmp_path / "plain"
    plain.mkdir()
    result = run_dispatch("no_such_hook", {}, tmp_path, None, plain)
    assert result.returncode == 0
    assert result.stdout.strip() == ""


@pytest.mark.skipif(SH is None, reason="sh not available")
def test_guard_broken_json_exits_zero_silently(tmp_path: Path, monkeypatch) -> None:
    plain = tmp_path / "plain"
    plain.mkdir()
    env = {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "CLAUDE_PLUGIN_DATA": str(tmp_path / "plugin-data"),
        "CORPUS_KB_HOOK_PYTHON": sys.executable,
    }
    result = subprocess.run(
        [str(SH), str(HOOKS / "dispatch.sh"), "session_start"],
        input="not json at all",
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
        env=env,
        cwd=str(plain),
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.strip() == ""


@pytest.mark.skipif(SH is None, reason="sh not available")
def test_session_start_runs_python_outside_study_folder(tmp_path: Path) -> None:
    """SessionStart has no walk-up guard: the daily prune must always run."""
    fake = write_fake_python(tmp_path)
    plain = tmp_path / "plain"
    plain.mkdir()
    marker = tmp_path / "marker"
    result = subprocess.run(
        [str(SH), str(HOOKS / "dispatch.sh"), "session_start"],
        input=json.dumps({"cwd": str(plain)}),
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
        env={
            "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
            "CLAUDE_PLUGIN_DATA": str(tmp_path / "plugin-data"),
            "CORPUS_KB_HOOK_PYTHON": str(fake),
            "FAKE_PYTHON_MARKER": str(marker),
        },
        cwd=str(plain),
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert marker.exists()


@pytest.mark.skipif(SH is None, reason="sh not available")
def test_pre_tool_use_starts_python_inside_study_folder(tmp_path: Path) -> None:
    fake = write_fake_python(tmp_path)
    root = tmp_path / "study"
    (root / ".claude").mkdir(parents=True)
    (root / ".claude" / "corpus.json").write_text('{"slug": "q3"}', encoding="utf-8")
    marker = tmp_path / "marker"
    result = subprocess.run(
        [str(SH), str(HOOKS / "dispatch.sh"), "pre_tool_use"],
        input=json.dumps({"cwd": str(root)}),
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
        env={
            "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
            "CLAUDE_PLUGIN_DATA": str(tmp_path / "plugin-data"),
            "CORPUS_KB_HOOK_PYTHON": str(fake),
            "FAKE_PYTHON_MARKER": str(marker),
        },
        cwd=str(root),
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert marker.exists()
