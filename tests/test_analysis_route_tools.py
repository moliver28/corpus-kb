"""The analysis_route handler: typed gates, envelope grammar, determinism.

The golden check freezes ONE full envelope as JSON (sort_keys) and
byte-compares it. Same question, same bytes, always — the determinism
contract. The embedder rides module seams (the pgml provider answers
``None``, the fakes answer fixed vectors), so no test here needs Ollama.
The register rides the one loader seam for the tiny-register cases (the
loader owns the 01-30 ledger; these rows skip it).
"""

from __future__ import annotations

import json
from typing import cast

import asyncpg
import pytest

from corpus_kb.analysis.method_register import MethodRegister
from corpus_kb.errors import (
    ERROR_MAP,
    NoMethodMatchesError,
    QuestionTooShortError,
    ValidationError,
)
from corpus_kb.tools import analysis_route_tools
from corpus_kb.tools.analysis_route_tools import analysis_route

# Four authored phrases of method 29: the phrase path scores 4/6 and
# resolves; the envelope keys stay identical in every embedder mode.
QUESTION = (
    "Find duplicate or paraphrased records before analysis. "
    "Which records say nearly the same thing? "
    "I want to merge near duplicates without losing speakers. "
    "Detect repeated submissions and rephrased copies."
)

# The frozen full envelope for the golden query (one line, sort_keys;
# the determinism pin). The pgml config answers phrase mode, so the
# envelope status is honestly "degraded" while the intent resolved.
# Regenerate by running the golden query and serializing with
# json.dumps(result, sort_keys=True).
GOLDEN_JSON = """{"intent": {"axes": {"comparison_structure": null, "cost_tier": null, "evidence_basis": null, "output_form": null, "statistical_rigor": null, "task_type": null}, "confidence": 0.6666666666666666, "method_id": "29", "note": "degraded", "question": "find duplicate or paraphrased records before analysis. which records say nearly the same thing? i want to merge near duplicates without losing speakers. detect repeated submissions and rephrased copies.", "ranked_method_ids": ["29"], "readings": [], "status": "resolved"}, "ranked": [{"method_id": "29", "name": "Near-duplicate and paraphrase detection", "run_hint": {"op": "duplicates_screen", "params": {}}, "why": "Find duplicate or paraphrased records before analysis."}], "status": "degraded", "trace": {"margin": 0.1, "mode": "phrase", "scores": [{"method_id": "29", "score": 0.6666666666666666}], "threshold": 0.55}}"""  # noqa: E501


class _FlatEmbedder:
    """Deterministic fake: every text maps to the same unit vector.

    All cosines collapse to 1.0, so multi-candidate questions tie and the
    margin rule answers ambiguous — deterministic, no Ollama.
    """

    def embed(self, text: str) -> list[float]:
        return [1.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0]


_PGML = {"embedding": {"provider": "pgml"}}

_TIE_A = "How do I cluster my documents by theme?"
_TIE_B = "How do I build a codebook for my interviews?"


def _record(id_: str, name: str, phrases: list[str] | None = None) -> dict[str, object]:
    """One minimal valid method row; ``phrases=None`` drops the routing field."""
    row: dict[str, object] = {
        "id": id_,
        "name": name,
        "summary": "A short active summary for the test row.",
        "paradigm": "unsupervised",
        "evidence_label": "model_based",
        "inputs_required": ["raw_text"],
        "calibration_effort": "low",
        "autonomy": "automated_with_audit",
        "complexity_tier": "baseline",
        "selection_priority": "first",
        "selection_notes": "First for the test need.",
        "robustness_caveat": "Results change with the input wording.",
        "bias_risk": "The method can favor common wording.",
        "efficiency_workload": "One pass over the records.",
        "output_family": "scores",
        "output_shape": "One score per record.",
        "usage_guidance": "Use it for quick screening.",
        "validation_required": "Check a sample of the scores.",
        "permitted_claims": "Screening claims only.",
        "do_not_claim": "Do not claim calibrated error rates.",
        "implementation_status": "not_planned",
        "op": None,
        "sources": ["https://example.com/method"],
    }
    if phrases is not None:
        row["question_phrases"] = phrases
    return row


def _register(*rows: dict[str, object]) -> MethodRegister:
    return MethodRegister.model_validate(
        {
            "register_version": "1.1.0",
            "source_document": {"title": "Test document", "path_note": "Test rows only."},
            "selection_rules": ["Match the required output."],
            "adjacent_candidates": "None.",
            "methods": list(rows),
        }
    )


class _ZeroEmbedder:
    """The degraded-embedder signal: every vector is the zero vector."""

    def embed(self, text: str) -> list[float]:
        return [0.0] * 8


async def _run(
    question: object = QUESTION,
    method_id: str | None = None,
    config: dict[str, object] | None = _PGML,
) -> dict[str, object]:
    return await analysis_route(
        cast("asyncpg.Pool", None),  # routing reads no database
        config=config,
        question=cast("str", question),  # the typed gate owns the wrong-type case
        method_id=method_id,
    )


async def test_non_string_question_rejected() -> None:
    with pytest.raises(ValidationError) as excinfo:
        await _run(question=12345)
    assert "question" in str(excinfo.value)
    assert excinfo.value.remediation


@pytest.mark.parametrize("question", ["", "   ", "short", "  abc  ", "7 chars"])
async def test_short_question_rejected(question: str) -> None:
    with pytest.raises(QuestionTooShortError) as excinfo:
        await _run(question=question)
    assert str(excinfo.value) == "Ask a question of at least 8 characters."
    assert excinfo.value.remediation


async def test_exact_minimum_question_accepted() -> None:
    """Eight characters after strip pass the gate (off-by-one freeze)."""
    result = await _run(question="8 chars!")
    assert set(result) >= {"status", "intent", "ranked", "trace"}


async def test_unknown_override_rejected() -> None:
    with pytest.raises(ValidationError) as excinfo:
        await _run(method_id="99")
    assert "method_id" in str(excinfo.value)
    assert excinfo.value.remediation


async def test_phrase_mode_envelope_shape() -> None:
    """No sync embedder (pgml): the envelope is degraded, the intent resolved.

    This pins the amended envelope grammar: the degraded flag rides the
    envelope status AND ``intent.note``, while the decision itself stays
    on ``intent.status``.
    """
    result = await _run()
    assert set(result) == {"status", "intent", "ranked", "trace"}
    assert result["status"] == "degraded"
    ranked = cast("list[dict[str, object]]", result["ranked"])
    assert len(ranked) == 1
    top = ranked[0]
    assert top["method_id"] == "29"
    assert top["why"] == "Find duplicate or paraphrased records before analysis."
    assert top["run_hint"] == {"op": "duplicates_screen", "params": {}}
    intent = cast("dict[str, object]", result["intent"])
    assert intent["status"] == "resolved"
    assert intent["note"] == "degraded"
    assert intent["method_id"] == "29"
    trace = cast("dict[str, object]", result["trace"])
    assert trace["mode"] == "phrase"
    assert trace["threshold"] == 0.55
    assert trace["margin"] == 0.10


async def test_golden_envelope_is_byte_stable() -> None:
    """Two runs, one frozen envelope: same query, same bytes."""
    first = json.dumps(await _run(), sort_keys=True)
    second = json.dumps(await _run(), sort_keys=True)
    assert first == second == GOLDEN_JSON


async def test_ambiguous_envelope_lists_interpretations(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A tied cosine race answers ambiguous with the top readings.

    The flat fake keeps every cosine at 1.0, so the envelope status is a
    true ``ambiguous`` (not degraded) — the ladder grammar case.
    """
    monkeypatch.setattr(analysis_route_tools, "_router_embedder", lambda _cfg: _FlatEmbedder())
    register = _register(
        _record("05", "Cluster method", [_TIE_A, "Which cluster structure fits these records?"]),
        _record("01", "Codebook method", [_TIE_B, "Can a method help me code these transcripts?"]),
    )
    monkeypatch.setattr(analysis_route_tools, "load_method_register", lambda _cfg: register)
    result = await _run(question=f"{_TIE_A} {_TIE_B}", config={})
    assert result["status"] == "ambiguous"
    assert set(result) == {"status", "intent", "ranked", "trace", "interpretations"}
    readings = cast("list[dict[str, object]]", result["interpretations"])
    assert len(readings) == 2
    assert {str(row["method_id"]) for row in readings} == {"01", "05"}
    ranked = cast("list[dict[str, object]]", result["ranked"])
    assert len(ranked) == 2


async def test_degraded_ambiguity_keys_off_the_intent_status(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Degraded + ambiguous: envelope status degraded, interpretations stay."""
    monkeypatch.setattr(analysis_route_tools, "_router_embedder", lambda _cfg: _ZeroEmbedder())
    register = _register(
        _record("05", "Cluster method", [_TIE_A, "Which cluster structure fits these records?"]),
        _record("01", "Codebook method", [_TIE_B, "Can a method help me code these transcripts?"]),
    )
    monkeypatch.setattr(analysis_route_tools, "load_method_register", lambda _cfg: register)
    result = await _run(question=f"{_TIE_A} {_TIE_B}")
    assert result["status"] == "degraded"
    intent = cast("dict[str, object]", result["intent"])
    assert intent["note"] == "degraded"
    assert intent["status"] == "ambiguous"
    assert "interpretations" in result


async def test_insufficient_raises_no_method_matches(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    register = _register(_record("05", "Phrase-less method"))
    monkeypatch.setattr(analysis_route_tools, "load_method_register", lambda _cfg: register)
    with pytest.raises(NoMethodMatchesError) as excinfo:
        await _run(question="Is there any method that could help me here?")
    assert str(excinfo.value) == "No analysis method matches the question. Try other words."
    assert excinfo.value.remediation


async def test_override_forces_resolved(monkeypatch: pytest.MonkeyPatch) -> None:
    register = _register(
        _record("05", "Cluster method", [_TIE_A, "Which cluster structure fits these records?"]),
        _record("01", "Codebook method", [_TIE_B, "Can a method help me code these transcripts?"]),
    )
    monkeypatch.setattr(analysis_route_tools, "load_method_register", lambda _cfg: register)
    result = await _run(question=f"{_TIE_A} {_TIE_B}", method_id="01")
    assert result["status"] == "resolved"
    assert set(result) == {"status", "intent", "ranked", "trace"}
    intent = cast("dict[str, object]", result["intent"])
    assert intent["note"] == "expert_override"
    assert intent["method_id"] == "01"
    assert intent["confidence"] == 0.0
    ranked = cast("list[dict[str, object]]", result["ranked"])
    assert ranked[0]["method_id"] == "01"
    assert ranked[0]["why"] == "Set by the analyst."
    assert ranked[1]["method_id"] == "05"
    assert "interpretations" not in result


async def test_override_works_on_a_phraseless_register(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    register = _register(_record("05", "Phrase-less method"))
    monkeypatch.setattr(analysis_route_tools, "load_method_register", lambda _cfg: register)
    result = await _run(question="Is there any method that could help me here?", method_id="05")
    assert result["status"] == "resolved"
    intent = cast("dict[str, object]", result["intent"])
    assert intent["method_id"] == "05"


async def test_repeat_run_is_byte_identical_with_a_fake_embedder(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(analysis_route_tools, "_router_embedder", lambda _cfg: _FlatEmbedder())
    register = _register(
        _record("05", "Cluster method", [_TIE_A, "Which cluster structure fits these records?"]),
        _record("01", "Codebook method", [_TIE_B, "Can a method help me code these transcripts?"]),
    )
    monkeypatch.setattr(analysis_route_tools, "load_method_register", lambda _cfg: register)
    first = json.dumps(await _run(question=f"{_TIE_A} {_TIE_B}", config={}), sort_keys=True)
    second = json.dumps(await _run(question=f"{_TIE_A} {_TIE_B}", config={}), sort_keys=True)
    assert first == second


async def test_pgml_provider_takes_the_phrase_path() -> None:
    """No sync embedder for pgml today: the trace says phrase, never a lie."""
    result = await _run(config=_PGML)
    trace = cast("dict[str, object]", result["trace"])
    assert trace["mode"] == "phrase"


async def test_error_triples_match_the_error_map() -> None:
    assert ERROR_MAP["question_too_short"] == (400, -32602, 1)
    assert ERROR_MAP["no_method_matches"] == (400, -32602, 1)
    assert QuestionTooShortError.error_type == "question_too_short"
    assert NoMethodMatchesError.error_type == "no_method_matches"
    assert (QuestionTooShortError.http_status, QuestionTooShortError.socket_code) == (400, -32602)
    assert (NoMethodMatchesError.http_status, NoMethodMatchesError.socket_code) == (400, -32602)
