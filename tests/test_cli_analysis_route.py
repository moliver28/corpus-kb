"""The analysis-route CLI emit helper: human text mirrors the envelope.

Text mode must surface the ambiguity ladder (method id + why per
reading), so the "re-run with your pick" instruction is followable:
the printed ids are exactly what ``--method-id`` accepts. A decided
answer also prints the chosen method's next questions, verbatim, and
its next-method chain as ``try next`` lines (the ``corpus-kb`` run
command, then the why).
"""

from __future__ import annotations

import pytest

from corpus_kb.cli.cli_analysis_route import _route_emit

_AMBIGUOUS: dict[str, object] = {
    "status": "ambiguous",
    "intent": {"status": "ambiguous", "note": None},
    "ranked": [
        {
            "method_id": "05",
            "name": "Cluster method",
            "why": "How do I cluster my documents by theme?",
            "run_hint": {"op": None, "params": {}},
        }
    ],
    "interpretations": [
        {
            "method_id": "05",
            "method_name": "Cluster method",
            "why": "How do I cluster my documents by theme?",
        },
        {
            "method_id": "01",
            "method_name": "Codebook method",
            "why": "How do I build a codebook for my interviews?",
        },
    ],
    "trace": {"mode": "phrase", "threshold": 0.55, "margin": 0.1, "scores": []},
}


def test_text_mode_lists_the_reading_ids(capsys: pytest.CaptureFixture[str]) -> None:
    """Ambiguous answers print each reading id + why for --method-id."""
    _route_emit(_AMBIGUOUS, json_output=False)
    out = capsys.readouterr().out
    assert "analysis-route: ambiguous" in out
    assert "  readings:" in out
    assert "- 05 (Cluster method)" in out
    assert "- 01 (Codebook method)" in out
    # Ambiguous: no method is chosen, so no next questions and no chain.
    assert "next questions:" not in out
    assert "try next:" not in out


def test_text_mode_resolved_has_no_readings_block(capsys: pytest.CaptureFixture[str]) -> None:
    resolved: dict[str, object] = {
        "status": "resolved",
        "intent": {"status": "resolved", "note": None},
        "ranked": [
            {
                "method_id": "29",
                "name": "Near-duplicate and paraphrase detection",
                "why": "Find duplicate or paraphrased records before analysis.",
                "run_hint": {"op": "duplicates_screen", "params": {}},
            }
        ],
        "interpretations": [],
        "suggestions": [
            "Which near duplicates are genuinely separate speakers?",
            "How do thresholds split review from merge?",
        ],
        "trace": {"mode": "phrase", "threshold": 0.55, "margin": 0.1, "scores": []},
    }
    _route_emit(resolved, json_output=False)
    out = capsys.readouterr().out
    assert "analysis-route: resolved" in out
    assert "method: 29 (Near-duplicate and paraphrase detection)" in out
    assert "readings:" not in out
    # The chosen method's follow-up questions print verbatim.
    assert "  next questions:" in out
    assert "- Which near duplicates are genuinely separate speakers?" in out
    assert "- How do thresholds split review from merge?" in out


def test_text_mode_resolved_without_suggestions_has_no_block(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """No ``suggests`` on the chosen row: no next-questions block."""
    resolved: dict[str, object] = {
        "status": "resolved",
        "intent": {"status": "resolved", "note": None},
        "ranked": [
            {
                "method_id": "29",
                "name": "Near-duplicate and paraphrase detection",
                "why": "Find duplicate or paraphrased records before analysis.",
                "run_hint": {"op": "duplicates_screen", "params": {}},
            }
        ],
        "interpretations": [],
        "trace": {"mode": "phrase", "threshold": 0.55, "margin": 0.1, "scores": []},
    }
    _route_emit(resolved, json_output=False)
    out = capsys.readouterr().out
    assert "next questions:" not in out


def test_json_mode_prints_the_envelope_verbatim(capsys: pytest.CaptureFixture[str]) -> None:
    _route_emit(_AMBIGUOUS, json_output=True)
    out = capsys.readouterr().out
    assert '"status": "ambiguous"' in out


_NEXT_METHODS: dict[str, object] = {
    "status": "resolved",
    "intent": {"status": "resolved", "note": None},
    "ranked": [
        {
            "method_id": "04",
            "name": "Label build",
            "why": "How do I build labels for my interviews?",
            "run_hint": {"op": "label_build", "params": {}},
        }
    ],
    "interpretations": [],
    "suggestions": ["Which labels cover speaker turns?"],
    "next_methods": [
        {
            "method_id": "13",
            "title": "Centroid classify",
            "why": "Build on this method with Centroid classify (novice level).",
            "run": "corpus-kb centroids-classify",
        },
        {
            "method_id": "14",
            "title": "Neighbor classify",
            "why": "Build on this method with Neighbor classify (novice level).",
            "run": "corpus-kb neighbors-classify",
        },
    ],
    "trace": {"mode": "phrase", "threshold": 0.55, "margin": 0.1, "scores": []},
}


def test_text_mode_resolved_prints_the_next_method_chain(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A decided answer prints its chain: one run command + why per row."""
    _route_emit(_NEXT_METHODS, json_output=False)
    out = capsys.readouterr().out
    assert "  try next:" in out
    assert (
        "- corpus-kb centroids-classify: Build on this method with "
        "Centroid classify (novice level)." in out
    )
    assert "- corpus-kb neighbors-classify: Build on this method with" in out


def test_text_mode_empty_next_methods_prints_no_try_next_block(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Honest empties: an empty chain or a missing key prints no block."""
    _route_emit(dict(_NEXT_METHODS, next_methods=[]), json_output=False)
    assert "try next:" not in capsys.readouterr().out
    _route_emit({"status": "degraded", "next_methods": []}, json_output=False)
    assert "try next:" not in capsys.readouterr().out
    stripped = {key: value for key, value in _NEXT_METHODS.items() if key != "next_methods"}
    _route_emit(stripped, json_output=False)
    assert "try next:" not in capsys.readouterr().out
