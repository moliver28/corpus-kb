"""Behavior tests for the guided setup offer (issue #353).

Once per session, research-state only, gate-checked, and the trigger
list decides the UserPromptSubmit lane. The signal scan runs inside its
0.6 s budget and skips hidden and dependency folders.
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

PLUGIN = str(Path(__file__).resolve().parents[1])
if PLUGIN not in sys.path:
    sys.path.insert(0, PLUGIN)

import gate_state  # noqa: E402
import hook_state  # noqa: E402
import setup_offer  # noqa: E402
import triggers  # noqa: E402


def setup_state(tmp_path: Path, monkeypatch) -> Path:
    data = tmp_path / "plugin-data"
    monkeypatch.setenv("CLAUDE_PLUGIN_DATA", str(data))
    return data


def research_folder(tmp_path: Path) -> Path:
    folder = tmp_path / "Study Interviews"
    folder.mkdir()
    (folder / "p1-transcript.md").write_text(
        "P1: Hello.\nP2: Hi.\nP1: Next.\nP2: Go.\n", encoding="utf-8"
    )
    return folder


def startup_data(folder: Path, session: str = "s1") -> dict:
    return {"cwd": str(folder), "session_id": session, "hook_event_name": "startup"}


# ---------------------------------------------------------------------------
# Once per session
# ---------------------------------------------------------------------------


def test_offer_once_per_session(tmp_path: Path, monkeypatch) -> None:
    setup_state(tmp_path, monkeypatch)
    folder = research_folder(tmp_path)
    first = setup_offer.offer_for_session_start(startup_data(folder), folder)
    assert first is not None
    assert setup_offer.offer_for_session_start(startup_data(folder), folder) is None
    # A different session is offered again.
    assert setup_offer.offer_for_session_start(startup_data(folder, "s2")) is not None


def test_offer_once_without_session_id_uses_day_mark(tmp_path: Path, monkeypatch) -> None:
    setup_state(tmp_path, monkeypatch)
    folder = research_folder(tmp_path)
    first = setup_offer.offer_for_session_start({"cwd": str(folder)}, folder)
    second = setup_offer.offer_for_session_start({"cwd": str(folder)}, folder)
    assert first is not None
    assert second is None


def test_offer_marks_the_session_record(tmp_path: Path, monkeypatch) -> None:
    setup_state(tmp_path, monkeypatch)
    folder = research_folder(tmp_path)
    setup_offer.offer_for_session_start(startup_data(folder), folder)
    session = hook_state.load_session("s1")
    assert session is not None and session.get("setup_offered") is True


# ---------------------------------------------------------------------------
# Folder-state gates
# ---------------------------------------------------------------------------


def test_offer_silent_with_corpus_json(tmp_path: Path, monkeypatch) -> None:
    setup_state(tmp_path, monkeypatch)
    folder = research_folder(tmp_path)
    (folder / ".claude").mkdir()
    (folder / ".claude" / "corpus.json").write_text('{"slug": "x"}', encoding="utf-8")
    assert setup_offer.offer_for_session_start(startup_data(folder), folder) is None


def test_offer_silent_in_engine_repo(tmp_path: Path, monkeypatch) -> None:
    setup_state(tmp_path, monkeypatch)
    folder = research_folder(tmp_path)
    (folder / "src" / "corpus_kb").mkdir(parents=True)
    assert setup_offer.offer_for_session_start(startup_data(folder), folder) is None


def test_offer_silent_when_declined(tmp_path: Path, monkeypatch) -> None:
    setup_state(tmp_path, monkeypatch)
    folder = research_folder(tmp_path)
    gate_state.decline(folder)
    assert setup_offer.offer_for_session_start(startup_data(folder), folder) is None


def test_offer_silent_when_snoozed_and_back_after_expiry(
    tmp_path: Path, monkeypatch
) -> None:
    setup_state(tmp_path, monkeypatch)
    folder = research_folder(tmp_path)
    gate_state.snooze(folder, days=7)
    assert setup_offer.offer_for_session_start(startup_data(folder), folder) is None
    # Expire the snooze: the offer comes back.
    state = gate_state.load_state()
    state["folders"][gate_state.folder_key(folder)]["snoozed_until"] = "2020-01-01"
    gate_state.save_state(state)
    assert setup_offer.offer_for_session_start(startup_data(folder), folder) is not None


def test_offer_silent_without_signal(tmp_path: Path, monkeypatch) -> None:
    setup_state(tmp_path, monkeypatch)
    folder = tmp_path / "empty"
    folder.mkdir()
    assert setup_offer.offer_for_session_start(startup_data(folder), folder) is None


def test_offer_text_asks_one_question(tmp_path: Path, monkeypatch) -> None:
    setup_state(tmp_path, monkeypatch)
    folder = research_folder(tmp_path)
    text = setup_offer.offer_for_session_start(startup_data(folder), folder)
    assert text is not None
    assert "Finish the user's current request first" in text
    assert "corpus-rag snooze" in text
    assert "corpus-rag decline" in text


def test_offer_writes_the_research_sentinel(tmp_path: Path, monkeypatch) -> None:
    setup_state(tmp_path, monkeypatch)
    folder = research_folder(tmp_path)
    setup_offer.offer_for_session_start(startup_data(folder), folder)
    assert setup_offer.sentinel_present(folder)
    setup_offer.remove_sentinel(folder)
    assert not setup_offer.sentinel_present(folder)


def test_find_sentinel_walks_up(tmp_path: Path, monkeypatch) -> None:
    setup_state(tmp_path, monkeypatch)
    folder = research_folder(tmp_path)
    setup_offer.touch_sentinel(folder)
    nested = folder / "interviews" / "monday"
    nested.mkdir(parents=True)
    assert setup_offer.find_sentinel(nested) == folder.resolve()


# ---------------------------------------------------------------------------
# The prompt lane: trigger list
# ---------------------------------------------------------------------------


def test_prompt_offer_requires_trigger_match(tmp_path: Path, monkeypatch) -> None:
    setup_state(tmp_path, monkeypatch)
    folder = research_folder(tmp_path)
    setup_offer.touch_sentinel(folder)
    plain = setup_offer.offer_for_prompt({"cwd": str(folder)}, folder, "hello world")
    assert plain is None
    hit = setup_offer.offer_for_prompt(
        {"cwd": str(folder)}, folder, "can you summarize the transcripts?"
    )
    assert hit is not None


def test_prompt_offer_once_per_session(tmp_path: Path, monkeypatch) -> None:
    setup_state(tmp_path, monkeypatch)
    folder = research_folder(tmp_path)
    setup_offer.touch_sentinel(folder)
    data = {"cwd": str(folder), "session_id": "sp"}
    prompt = "analyze the participant interviews please"
    assert setup_offer.offer_for_prompt(data, folder, prompt) is not None
    assert setup_offer.offer_for_prompt(data, folder, prompt) is None


# ---------------------------------------------------------------------------
# Signal scan: budget, exclusions, counts
# ---------------------------------------------------------------------------


def test_signal_scan_counts_and_skips(tmp_path: Path, monkeypatch) -> None:
    setup_state(tmp_path, monkeypatch)
    folder = research_folder(tmp_path)
    (folder / "interview-summaries.md").write_text("notes", encoding="utf-8")
    (folder / "node_modules").mkdir(parents=True)
    (folder / "node_modules" / "x.md").write_text("P1: a. P2: b.", encoding="utf-8")
    (folder / "__pycache__").mkdir()
    (folder / "__pycache__" / "y.md").write_text("P1: a. P2: b.", encoding="utf-8")
    (folder / ".hidden").mkdir()
    (folder / ".hidden" / "z.md").write_text("P1: a. P2: b.", encoding="utf-8")
    counts = setup_offer.signal_scan(folder)
    assert counts["transcripts"] >= 1
    assert counts["interview_summaries"] == 1
    joined = json.dumps(counts)
    assert "node_modules" not in joined


def test_signal_scan_within_budget(tmp_path: Path, monkeypatch) -> None:
    setup_state(tmp_path, monkeypatch)
    folder = research_folder(tmp_path)
    for index in range(50):
        (folder / f"f{index:03}.md").write_text("x", encoding="utf-8")
    counts = setup_offer.signal_scan(folder)
    assert counts["elapsed_s"] < 2.0
    assert setup_offer.has_signal(counts)


def test_prompt_matching_uses_the_trigger_file(tmp_path: Path) -> None:
    # The fixture pin lives in test_triggers; here the shipped file works.
    data = triggers.load_triggers()
    assert data["nouns"] and data["frames"] and data["exclusions"]
    assert triggers.matches("summarize what the participant said")
    assert not triggers.matches("fix the embedding model in this repo")


def test_dedup_mark_fallback_expires(tmp_path: Path, monkeypatch) -> None:
    setup_state(tmp_path, monkeypatch)
    folder = research_folder(tmp_path)
    day = time.strftime("%Y%m%d")
    import zlib

    name = f"setup-offer-{zlib.crc32(str(folder).encode('utf-8'))}-{day}"
    hook_state.touch_mark(name)
    assert setup_offer.offered_this_session({"cwd": str(folder)}, folder) is True
