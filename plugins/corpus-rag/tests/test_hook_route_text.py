"""Route-derived nudge text for the analyze lane (plan Task 4).

``hook_route_text.route_nudge_text`` turns an ``analysis-route``
envelope into two plain lines: the top method with its why, then the
run command from ``run_hint``. Degraded routes say so. Any malformed
route yields an empty string, so the caller keeps the static lane
text. Stdlib only.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

PLUGIN = str(Path(__file__).resolve().parents[1])
if PLUGIN not in sys.path:
    sys.path.insert(0, PLUGIN)

import hook_route_text  # noqa: E402


def route(**over) -> dict:
    row = {
        "method_id": "sentiment-screen",
        "name": "Sentiment screen",
        "why": "The question asks how participants feel per unit.",
        "run_hint": {
            "op": "sentiment_screen",
            "params": {"project_id": "interviews-2026"},
        },
    }
    return {"status": "resolved", "ranked": [{**row, **over.pop("row", {})}], **over}


def test_happy_path_names_top_method_and_command() -> None:
    text = hook_route_text.route_nudge_text(route(), "how do they feel?")
    lines = text.splitlines()
    assert len(lines) == 2
    assert "Sentiment screen" in lines[0]
    assert "feel" in lines[0]
    assert "corpus-rag op sentiment_screen" in lines[1]
    assert "project_id=interviews-2026" in lines[1]


def test_degraded_route_says_so() -> None:
    text = hook_route_text.route_nudge_text(route(status="degraded"), "q")
    assert text.startswith("Routing ran in degraded mode.")


def test_malformed_route_yields_empty() -> None:
    for bad in ({}, {"ranked": []}, {"ranked": "no"}, {"ranked": [{}]},
                {"ranked": [{"name": "x"}]}, "json", None, {"ranked": [{"name": "n", "run_hint": {}}]}):
        assert hook_route_text.route_nudge_text(bad, "q") == ""


def test_empty_ranked_yields_empty() -> None:
    assert hook_route_text.route_nudge_text({"status": "resolved", "ranked": []}, "q") == ""


def test_first_line_stays_short() -> None:
    long_why = "word " * 60
    text = hook_route_text.route_nudge_text(route(row={"why": long_why.strip()}), "q")
    first = text.splitlines()[0]
    assert len(first.split()) <= 20


def test_params_render_as_key_value_pairs() -> None:
    text = hook_route_text.route_nudge_text(
        route(row={"run_hint": {"op": "lexical_classify", "params": {"k": 5}}}), "q"
    )
    assert "corpus-rag op lexical_classify" in text
    assert "k=5" in text


def test_missing_params_still_renders_command() -> None:
    text = hook_route_text.route_nudge_text(
        route(row={"run_hint": {"op": "outliers_rank"}}), "q"
    )
    assert "corpus-rag op outliers_rank" in text


# ---------------------------------------------------------------------------
# lane wiring: route success, timeout, and failure keep the static text
# ---------------------------------------------------------------------------

import hook_events  # noqa: E402


def corpus_root(tmp_path: Path, monkeypatch, slug: str = "q3") -> Path:
    data = tmp_path / "plugin-data"
    monkeypatch.setenv("CLAUDE_PLUGIN_DATA", str(data))
    root = tmp_path / "study"
    (root / ".claude").mkdir(parents=True)
    marker = {"slug": slug, "watch": []}
    (root / ".claude" / "corpus.json").write_text(json.dumps(marker), encoding="utf-8")
    (data / "studies.json").parent.mkdir(parents=True, exist_ok=True)
    (data / "studies.json").write_text(
        json.dumps(
            {"active": None, "studies": {slug: {"database_url": "x", "port": 8010}}}
        ),
        encoding="utf-8",
    )
    return root


class _Done:
    def __init__(self, stdout: str) -> None:
        self.stdout = stdout
        self.stderr = ""
        self.returncode = 0


def wire_stub(monkeypatch, stdout: str) -> list:
    calls: list = []
    monkeypatch.setattr(hook_events.engine_paths, "engine_cli", lambda root: "corpus-kb")
    monkeypatch.setattr(
        hook_events.subprocess,
        "run",
        lambda argv, **kw: calls.append(argv) or _Done(stdout),
    )
    return calls


def test_lane_nudge_uses_route_text(tmp_path: Path, monkeypatch) -> None:
    root = corpus_root(tmp_path, monkeypatch)
    envelope = {
        "status": "resolved",
        "ranked": [
            {
                "method_id": "s",
                "name": "Sentiment screen",
                "why": "it fits.",
                "run_hint": {"op": "sentiment_screen", "params": {}},
            }
        ],
    }
    wire_stub(monkeypatch, json.dumps(envelope))
    fs = hook_events.folder_state.folder_state(root, scan=False)
    text = hook_events.lane_nudge("how do i analyze this?", fs)
    assert text is not None and "Sentiment screen" in text


def test_lane_nudge_timeout_keeps_static_text(tmp_path: Path, monkeypatch) -> None:
    root = corpus_root(tmp_path, monkeypatch)

    def boom(argv, **kw):
        raise hook_events.subprocess.TimeoutExpired(cmd=argv, timeout=10)

    monkeypatch.setattr(hook_events.engine_paths, "engine_cli", lambda root: "corpus-kb")
    monkeypatch.setattr(hook_events.subprocess, "run", boom)
    fs = hook_events.folder_state.folder_state(root, scan=False)
    text = hook_events.lane_nudge("how do i analyze this?", fs)
    assert text is not None
    assert "analysis-select-method" in text


def test_lane_nudge_bad_json_keeps_static_text(tmp_path: Path, monkeypatch) -> None:
    root = corpus_root(tmp_path, monkeypatch)
    wire_stub(monkeypatch, "not json")
    fs = hook_events.folder_state.folder_state(root, scan=False)
    text = hook_events.lane_nudge("how do i analyze this?", fs)
    assert text is not None
    assert "analysis-select-method" in text


def test_route_runs_after_the_mark_gate(tmp_path: Path, monkeypatch) -> None:
    root = corpus_root(tmp_path, monkeypatch)
    calls = wire_stub(monkeypatch, json.dumps({"status": "resolved", "ranked": [
        {"method_id": "s", "name": "N", "why": "w", "run_hint": {"op": "o"}}]}))
    fs = hook_events.folder_state.folder_state(root, scan=False)
    first = hook_events.lane_nudge("how do i analyze this?", fs)
    second = hook_events.lane_nudge("how do i analyze this?", fs)
    assert first is not None and second is None
    assert len(calls) == 1
