"""Behavior tests for the setup-offer trigger list (issue #353).

Fixture sets (``should_use`` / ``should_ignore``) pin the matcher; the
term shapes (word-prefix ``*``, ``a ... b`` within 80 characters, and
``{term, not: []}`` blocked prefixes) have one test each.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

PLUGIN = str(Path(__file__).resolve().parents[1])
if PLUGIN not in sys.path:
    sys.path.insert(0, PLUGIN)

import triggers  # noqa: E402

SHOULD_USE = [
    "can you summarize the participant transcripts?",
    "Analyze the interviews from Monday",
    "what did the participants say about pricing",
    "synthesize the insights across sessions",
    "analyze quotes that support the hypothesis",
    "map the research question onto the codebook and analyze it",
    "start analyzing what the participants told us",
    "what did the interviewees say should change",
]

SHOULD_IGNORE = [
    "fix the failing test in this repo",
    "how do the embedding dimensions work",
    "write an interview script for tomorrow",
    "draft a discussion guide for the session",
    "summarize this code file",
    "hello world",
    "",
]


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def test_should_use_fixture_set() -> None:
    for prompt in SHOULD_USE:
        assert triggers.matches(prompt), prompt


def test_should_ignore_fixture_set() -> None:
    for prompt in SHOULD_IGNORE:
        assert not triggers.matches(prompt), prompt


def test_prompt_terms_reports_each_kind() -> None:
    terms = triggers.prompt_terms("summarize the participant transcripts")
    assert "transcript*" in terms["nouns"]
    assert "participant*" in terms["nouns"]
    assert "summarize" in terms["frames"]
    assert terms["exclusions"] == []


# ---------------------------------------------------------------------------
# Term shapes
# ---------------------------------------------------------------------------


def test_star_is_a_word_prefix_wildcard() -> None:
    assert triggers.matches("analyze the transcripts")
    assert triggers.matches("analyze this transcript")
    assert triggers.matches("let us analyze the transcription error")


def test_ellipsis_allows_up_to_80_characters() -> None:
    assert triggers.matches("what did the participants say about price")
    long_gap = "what did " + ("x" * 60) + " participants say"
    assert triggers.matches(long_gap)
    too_long = "what did " + ("x" * 90) + " participants say"
    assert not triggers.matches(too_long)


def test_not_prefix_blocks_listed_words(tmp_path: Path) -> None:
    data = {
        "nouns": [{"term": "inter*", "not": ["interv"]}],
        "frames": ["analy*"],
        "exclusions": [],
    }
    root = tmp_path / "plugin"
    (root / "hooks").mkdir(parents=True)
    (root / "hooks" / "triggers.json").write_text(json.dumps(data), encoding="utf-8")
    # "inter*" with the "interv" prefix blocked: "international" counts,
    # "interview" never does.
    assert triggers.matches("analyze the international report", plugin_root=root)
    assert not triggers.matches("analyze the interview room", plugin_root=root)


def test_exclusions_win_over_matches() -> None:
    assert triggers.matches("analyze the participant transcripts")
    assert not triggers.matches("analyze the interview script transcripts")


def test_missing_trigger_file_matches_nothing(tmp_path: Path) -> None:
    assert triggers.load_triggers(tmp_path) == {
        "nouns": [],
        "frames": [],
        "exclusions": [],
    }
    assert not triggers.matches("analyze the transcripts", plugin_root=tmp_path)
