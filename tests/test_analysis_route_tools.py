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
from pathlib import Path
from typing import cast

import asyncpg
import pytest

from corpus_kb.analysis.method_register import MethodRegister
from corpus_kb.analysis.method_register_loader import load_method_register
from corpus_kb.analysis.method_register_rubric import rank_methods
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
GOLDEN_JSON = """{"intent": {"axes": {"comparison_structure": null, "cost_tier": null, "evidence_basis": null, "output_form": null, "statistical_rigor": null, "task_type": null}, "confidence": 0.6666666666666666, "method_id": "29", "note": "degraded", "question": "find duplicate or paraphrased records before analysis. which records say nearly the same thing? i want to merge near duplicates without losing speakers. detect repeated submissions and rephrased copies.", "ranked_method_ids": ["29"], "readings": [], "status": "resolved"}, "next_methods": [], "ranked": [{"method_id": "29", "name": "Near-duplicate and paraphrase detection", "run_hint": {"op": "duplicates_screen", "params": {}}, "why": "Find duplicate or paraphrased records before analysis."}], "status": "degraded", "suggestions": ["Which near duplicates are genuinely separate speakers?", "How do thresholds split review from merge?", "What paraphrases survived the exact-match pass?"], "trace": {"margin": 0.1, "mode": "phrase", "scores": [{"method_id": "29", "score": 0.6666666666666666}], "threshold": 0.55}}"""  # noqa: E501


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

# Two filler phrases per id for the six-row fallback-pool fixture: none
# may contain "cluster" (the pool winner's direction) or overlap the
# pool question ("I want a cluster breakdown of my interview notes.").
_FILLER_PHRASES = {
    "01": ("Which first codes anchor the book?", "How do codes split by speaker?"),
    "02": ("What themes recur across the interviews?", "Which quotes carry the theme?"),
    "03": ("How do the topics drift over time?", "Which months shift the topic mix?"),
    "04": ("What errors repeat in the transcripts?", "Which mistakes recur weekly?"),
    "05": ("How do the speakers contradict each other?", "Which pairs disagree most?"),
}


def _record(
    id_: str,
    name: str,
    phrases: list[str] | None = None,
    suggests: list[str] | None = None,
    *,
    op: str | None = None,
    difficulty: str | None = None,
    next_: list[str] | None = None,
) -> dict[str, object]:
    """One minimal valid method row; ``None`` drops the routing field."""
    status = "implemented_existing" if op is not None else "not_planned"
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
        "implementation_status": status,
        "op": op,
        "sources": ["https://example.com/method"],
    }
    if phrases is not None:
        row["question_phrases"] = phrases
    if suggests is not None:
        row["suggests"] = suggests
    if difficulty is not None:
        row["difficulty"] = difficulty
    if next_ is not None:
        row["next"] = next_
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


class _SplitEmbedder:
    """Cluster-flavored text maps to e1, everything else to e2.

    The cluster centroid and the cluster question share one direction, so
    the embedding path picks a clear winner: status ``resolved`` with no
    degradation. Deterministic, no Ollama.
    """

    def embed(self, text: str) -> list[float]:
        if "cluster" in text.casefold():
            return [1.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0]
        return [0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0]


# Method 29's authored next questions, verbatim from the register YAML
# (analysis_methods.yaml). The envelope copies them, never rewords them.
_S29_SUGGESTS = [
    "Which near duplicates are genuinely separate speakers?",
    "How do thresholds split review from merge?",
    "What paraphrases survived the exact-match pass?",
]


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
    assert set(result) == {
        "status",
        "intent",
        "ranked",
        "next_methods",
        "trace",
        "suggestions",
    }
    assert result["next_methods"] == []
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


# -- Suggestion presence (routing W4): the chips ride the CHOSEN method.
#    Present iff the intent resolved AND a method is chosen — the override
#    path and the degraded-but-decided phrase path included. Absent on
#    ambiguous: no method is chosen yet, the ladder is the answer.


async def test_degraded_resolved_envelope_carries_the_register_suggestions() -> None:
    """The phrase-path answer (degraded envelope, resolved intent) suggests.

    CI and no-Ollama hosts take this path; the chips must survive it. The
    strings are method 29's register rows, verbatim.
    """
    result = await _run()
    assert result["status"] == "degraded"
    intent = cast("dict[str, object]", result["intent"])
    assert intent["status"] == "resolved"
    assert intent["method_id"] == "29"
    assert result["suggestions"] == _S29_SUGGESTS


async def test_healthy_resolved_envelope_carries_the_chosen_suggestions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A plain resolved answer (embedding mode, no degradation) suggests.

    The winner is method 05; its three authored strings ride verbatim,
    the runner-up's strings never appear.
    """
    monkeypatch.setattr(analysis_route_tools, "_router_embedder", lambda _cfg: _SplitEmbedder())
    cluster_suggests = [
        "Which cluster count fits these interviews?",
        "How stable are the themes across runs?",
        "What do the outliers say about the topics?",
    ]
    register = _register(
        _record(
            "05",
            "Cluster method",
            [_TIE_A, "Which cluster structure fits these records?"],
            suggests=cluster_suggests,
        ),
        _record(
            "01",
            "Codebook method",
            [_TIE_B, "Can a method help me code these transcripts?"],
            suggests=["Which first codes anchor the book?"],
        ),
    )
    monkeypatch.setattr(analysis_route_tools, "load_method_register", lambda _cfg: register)
    result = await _run(question=_TIE_A, config={})
    assert result["status"] == "resolved"
    assert result["suggestions"] == cluster_suggests


async def test_suggestions_follow_the_chosen_method_not_the_rubric_top(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Score winner and rubric order can disagree: chips follow the score.

    The question phrase-hits a phrase of each row, so both ride the
    candidate set. Both rows share every rubric axis, so the rubric
    sorts 01 first; the embedding scores pick 05. The chips ride 05 —
    the intent's choice — never the rubric-top row.
    """
    monkeypatch.setattr(analysis_route_tools, "_router_embedder", lambda _cfg: _SplitEmbedder())
    cluster_suggests = [
        "Which cluster count fits these interviews?",
        "How stable are the themes across runs?",
    ]
    codebook_suggests = ["Which first codes anchor the book?"]
    register = _register(
        _record(
            "01",
            "Codebook method",
            [_TIE_B, "Can a method help me code these transcripts?"],
            suggests=codebook_suggests,
        ),
        _record(
            "05",
            "Cluster method",
            [_TIE_A, "Which cluster structure fits these records?"],
            suggests=cluster_suggests,
        ),
    )
    monkeypatch.setattr(analysis_route_tools, "load_method_register", lambda _cfg: register)
    result = await _run(question=f"{_TIE_A} {_TIE_B}", config={})
    assert result["status"] == "resolved"
    intent = cast("dict[str, object]", result["intent"])
    assert intent["method_id"] == "05"
    ranked = cast("list[dict[str, object]]", result["ranked"])
    assert ranked[0]["method_id"] == "01"
    assert result["suggestions"] == cluster_suggests


async def test_suggestions_survive_a_fallback_pool_bigger_than_the_ranked_cap(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The score winner can sit outside the capped ranked list.

    No phrase hits, so the fallback pool is every phrased row (six).
    The rubric sorts by id, so ranked caps out at 01-05; the embedding
    scores pick 06. The chips must still ride 06 — sourced from the
    register, never from the capped list.
    """
    monkeypatch.setattr(analysis_route_tools, "_router_embedder", lambda _cfg: _SplitEmbedder())
    cluster_suggests = [
        "Which cluster count fits these interviews?",
        "How do the near matches split?",
    ]
    register = _register(
        *[
            _record(
                id_,
                f"Filler method {id_}",
                [_FILLER_PHRASES[id_][0], _FILLER_PHRASES[id_][1]],
            )
            for id_ in ("01", "02", "03", "04", "05")
        ],
        _record(
            "06",
            "Cluster method",
            [
                "Which cluster count fits these interviews?",
                "How stable are the themes across runs?",
            ],
            suggests=cluster_suggests,
        ),
    )
    monkeypatch.setattr(analysis_route_tools, "load_method_register", lambda _cfg: register)
    result = await _run(question="I want a cluster breakdown of my interview notes.", config={})
    assert result["status"] == "resolved"
    intent = cast("dict[str, object]", result["intent"])
    assert intent["method_id"] == "06"
    ranked_ids = [
        cast("dict[str, object]", row)["method_id"]
        for row in cast("list[dict[str, object]]", result["ranked"])
    ]
    assert ranked_ids == ["01", "02", "03", "04", "05", "06"]
    assert ranked_ids.count("06") == 1
    assert result["suggestions"] == cluster_suggests


async def test_resolved_winner_always_has_a_row_in_ranked(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The chosen method keeps a ranked row even when the cap drops it.

    No phrase hits, so the fallback pool is every phrased row (six). The
    rubric sorts 01-05 into the capped list; the embedding scores pick
    06. The resolved answer's method can never be absent from ``ranked``:
    the envelope appends its row after the capped five, and the first
    five stay in rubric order. With no phrase hit, the appended why is
    the rubric reason — a name is never a reason.
    """
    monkeypatch.setattr(analysis_route_tools, "_router_embedder", lambda _cfg: _SplitEmbedder())
    register = _register(
        *[
            _record(id_, f"Filler method {id_}", [_FILLER_PHRASES[id_][0], _FILLER_PHRASES[id_][1]])
            for id_ in ("01", "02", "03", "04", "05")
        ],
        _record(
            "06",
            "Cluster method",
            [
                "Which cluster count fits these interviews?",
                "How stable are the themes across runs?",
            ],
        ),
    )
    monkeypatch.setattr(analysis_route_tools, "load_method_register", lambda _cfg: register)
    result = await _run(question="I want a cluster breakdown of my interview notes.", config={})
    assert result["status"] == "resolved"
    intent = cast("dict[str, object]", result["intent"])
    assert intent["method_id"] == "06"
    ranked = cast("list[dict[str, object]]", result["ranked"])
    ranked_ids = [str(row["method_id"]) for row in ranked]
    assert ranked_ids == ["01", "02", "03", "04", "05", "06"]
    winner = ranked[-1]
    assert winner["name"] == "Cluster method"
    assert winner["run_hint"] == {"op": None, "params": {}}
    rubric_reason = next(rec for rec in rank_methods(register) if rec.id == "06").rank_reason
    assert winner["why"] == rubric_reason


async def test_winner_row_why_carries_the_matched_phrase(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An appended winner states its matched phrase, like the top row.

    The question contains one authored phrase of every row, so all six
    ride the candidate set as hits. The embedding scores pick 06 (its
    only phrase is the cluster-flavored one); the rubric cap drops it.
    The appended row's why is the phrase 06 matched, verbatim.
    """
    monkeypatch.setattr(analysis_route_tools, "_router_embedder", lambda _cfg: _SplitEmbedder())
    winner_phrase = "Which cluster count fits these interviews?"
    register = _register(
        *[
            _record(id_, f"Filler method {id_}", [_FILLER_PHRASES[id_][0]])
            for id_ in ("01", "02", "03", "04", "05")
        ],
        _record("06", "Cluster method", [winner_phrase]),
    )
    monkeypatch.setattr(analysis_route_tools, "load_method_register", lambda _cfg: register)
    question = " ".join([_FILLER_PHRASES[id_][0] for id_ in ("01", "02", "03", "04", "05")])
    question = f"{question} {winner_phrase}"
    result = await _run(question=question, config={})
    assert result["status"] == "resolved"
    intent = cast("dict[str, object]", result["intent"])
    assert intent["method_id"] == "06"
    ranked = cast("list[dict[str, object]]", result["ranked"])
    ranked_ids = [str(row["method_id"]) for row in ranked]
    assert ranked_ids[:5] == ["01", "02", "03", "04", "05"]
    assert ranked_ids[-1] == "06"
    assert ranked[-1]["why"] == winner_phrase


async def test_ambiguous_envelope_omits_suggestions(monkeypatch: pytest.MonkeyPatch) -> None:
    """Ambiguous: no method is chosen yet, so no chips — even when every
    candidate row authors suggestions. The ladder is the answer."""
    monkeypatch.setattr(analysis_route_tools, "_router_embedder", lambda _cfg: _FlatEmbedder())
    register = _register(
        _record(
            "05",
            "Cluster method",
            [_TIE_A, "Which cluster structure fits these records?"],
            suggests=["Which cluster count fits these interviews?"],
        ),
        _record(
            "01",
            "Codebook method",
            [_TIE_B, "Can a method help me code these transcripts?"],
            suggests=["Which first codes anchor the book?"],
        ),
    )
    monkeypatch.setattr(analysis_route_tools, "load_method_register", lambda _cfg: register)
    result = await _run(question=f"{_TIE_A} {_TIE_B}", config={})
    assert result["status"] == "ambiguous"
    assert "interpretations" in result
    assert "suggestions" not in result


async def test_degraded_ambiguous_envelope_omits_suggestions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Degraded + ambiguous: the ladder stays, the chips stay absent.

    Degradation keeps a complete answer's chips (the resolved case), but
    an ambiguous answer never had a chosen method to suggest from.
    """
    monkeypatch.setattr(analysis_route_tools, "_router_embedder", lambda _cfg: _ZeroEmbedder())
    register = _register(
        _record(
            "05",
            "Cluster method",
            [_TIE_A, "Which cluster structure fits these records?"],
            suggests=["Which cluster count fits these interviews?"],
        ),
        _record(
            "01",
            "Codebook method",
            [_TIE_B, "Can a method help me code these transcripts?"],
            suggests=["Which first codes anchor the book?"],
        ),
    )
    monkeypatch.setattr(analysis_route_tools, "load_method_register", lambda _cfg: register)
    result = await _run(question=f"{_TIE_A} {_TIE_B}")
    assert result["status"] == "degraded"
    intent = cast("dict[str, object]", result["intent"])
    assert intent["status"] == "ambiguous"
    assert "interpretations" in result
    assert "suggestions" not in result


async def test_override_carries_the_override_records_suggestions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The override answer suggests from the OVERRIDE record, verbatim.

    The automatic race here is ambiguous (flat cosine), yet the analyst's
    choice decides — so the chips ride the chosen row.
    """
    monkeypatch.setattr(analysis_route_tools, "_router_embedder", lambda _cfg: _FlatEmbedder())
    codebook_suggests = [
        "Which first codes anchor the book?",
        "How do the codes split by speaker?",
        "When do two codes merge into one?",
    ]
    register = _register(
        _record(
            "05",
            "Cluster method",
            [_TIE_A, "Which cluster structure fits these records?"],
            suggests=["Which cluster count fits these interviews?"],
        ),
        _record(
            "01",
            "Codebook method",
            [_TIE_B, "Can a method help me code these transcripts?"],
            suggests=codebook_suggests,
        ),
    )
    monkeypatch.setattr(analysis_route_tools, "load_method_register", lambda _cfg: register)
    result = await _run(question=f"{_TIE_A} {_TIE_B}", method_id="01")
    assert result["status"] == "resolved"
    intent = cast("dict[str, object]", result["intent"])
    assert intent["note"] == "expert_override"
    assert result["suggestions"] == codebook_suggests


async def test_no_suggests_omits_the_key(monkeypatch: pytest.MonkeyPatch) -> None:
    """A chosen row with no ``suggests`` omits the key — never an empty list.

    The register schema caps ``suggests`` at one to three strings
    (test_suggests_reject_count_and_word_cap), so the handler's three-row
    cap is defensive only and no four-string fixture can exist here.
    """
    register = _register(_record("05", "Quiet method", [_TIE_A]))
    monkeypatch.setattr(analysis_route_tools, "load_method_register", lambda _cfg: register)
    result = await _run(question=_TIE_A)
    intent = cast("dict[str, object]", result["intent"])
    assert intent["status"] == "resolved"
    assert "suggestions" not in result


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
    assert set(result) == {
        "status",
        "intent",
        "ranked",
        "next_methods",
        "trace",
        "interpretations",
    }
    assert result["next_methods"] == []
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
    assert set(result) == {"status", "intent", "ranked", "next_methods", "trace"}
    assert result["next_methods"] == []
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


# -- Adapter config flow (fix round 3): the routing knobs must reach the
#    handler on EVERY surface, not only MCP and the CLI. The pgml provider
#    keeps the phrase path, so the stacked QUESTION scores 4/6 = 0.667:
#    a 0.9 threshold must flip the intent to ambiguous, a 0.2 threshold
#    must leave it resolved. monkeypatched load_config = startup config.


async def _http_route_with_config(
    monkeypatch: pytest.MonkeyPatch, threshold: float
) -> dict[str, object]:
    from starlette.testclient import TestClient

    import corpus_kb.api.research_http as research_http
    import corpus_kb.config as config_module
    from corpus_kb.api.http import create_http_app

    monkeypatch.setattr(
        config_module,
        "load_config",
        lambda: _threshold_config(threshold),
    )
    research_http.set_research_pool(object())  # identity-only; reads no data
    try:
        client = TestClient(create_http_app())
        response = client.post("/api/analysis/route", json={"question": QUESTION})
        assert response.status_code == 200, response.text
        envelope: dict[str, object] = response.json()
        return envelope
    finally:
        research_http.set_research_pool(None)


def _threshold_config(threshold: float) -> dict[str, object]:
    return {
        "embedding": {"provider": "pgml"},
        "analysis": {"routing": {"confidence_threshold": threshold}},
    }


async def test_http_adapter_passes_the_routing_threshold(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    envelope = await _http_route_with_config(monkeypatch, 0.9)
    intent = cast("dict[str, object]", envelope["intent"])
    assert intent["status"] == "ambiguous"


async def test_http_adapter_default_threshold_resolves(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    envelope = await _http_route_with_config(monkeypatch, 0.2)
    intent = cast("dict[str, object]", envelope["intent"])
    assert intent["status"] == "resolved"


async def test_socket_adapter_passes_the_routing_threshold(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import corpus_kb.api.research_http as research_http
    import corpus_kb.config as config_module
    from corpus_kb.api.socket import JSONRPCServer

    monkeypatch.setattr(
        config_module,
        "load_config",
        lambda: _threshold_config(0.9),
    )
    research_http.set_research_pool(object())
    try:
        server = JSONRPCServer()
        rpc = await server._process_request(
            json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "analysis_route",
                    "params": {"question": QUESTION},
                }
            )
        )
    finally:
        research_http.set_research_pool(None)
    assert "result" in rpc, rpc
    intent = cast("dict[str, object]", rpc["result"]["intent"])
    assert intent["status"] == "ambiguous"


def test_rank_reason_never_masquerades_as_a_name() -> None:
    """No phrase hit: the top why is the rubric reason, not the name."""
    register = load_method_register({})
    top = rank_methods(register)[0]
    assert top.rank_reason != top.name


async def test_error_triples_match_the_error_map() -> None:
    assert ERROR_MAP["question_too_short"] == (400, -32602, 1)
    assert ERROR_MAP["no_method_matches"] == (400, -32602, 1)
    assert QuestionTooShortError.error_type == "question_too_short"
    assert NoMethodMatchesError.error_type == "no_method_matches"
    assert (QuestionTooShortError.http_status, QuestionTooShortError.socket_code) == (400, -32602)
    assert (NoMethodMatchesError.http_status, NoMethodMatchesError.socket_code) == (400, -32602)


# -- LLM fallback (routing W5): an ambiguous answer with the flag on asks
#    the ask-op LLM seam once, sanity-gates the answer against the register
#    and the six vocab tuples, and logs every attempt to a JSONL file.
#    Flag off: the envelope stays byte-identical to W4 and nothing logs.


class _ScriptedChat:
    """The ask-op chat seam stub: one scripted reply, a call counter."""

    def __init__(self, content: str = "", error: str | None = None) -> None:
        self.calls = 0
        self.script = content
        self._error = error

    async def chat(
        self,
        messages: list[dict[str, str]],
        model: str | None = None,
        options: dict[str, object] | None = None,
    ) -> dict[str, object]:
        self.calls += 1
        if self._error is not None:
            return {"error": self._error, "model": "stub"}
        return {"message": {"content": self.script}, "model": "stub"}


_TIE_QUESTION = f"{_TIE_A} {_TIE_B}"


def _ambiguous_register() -> MethodRegister:
    """Two phrased rows; the flat embedder ties them into an ambiguity."""
    return _register(
        _record(
            "05",
            "Cluster method",
            [_TIE_A, "Which cluster structure fits these records?"],
            suggests=["Which cluster count fits these interviews?"],
        ),
        _record(
            "01",
            "Codebook method",
            [_TIE_B, "Can a method help me code these transcripts?"],
            suggests=["Which first codes anchor the book?"],
        ),
    )


async def _ambiguous_run(
    monkeypatch: pytest.MonkeyPatch,
    chat: _ScriptedChat,
    config: dict[str, object],
    method_id: str | None = None,
) -> dict[str, object]:
    """One ambiguous route with the flag on and the chat seam stubbed."""
    monkeypatch.setattr(
        analysis_route_tools, "load_method_register", lambda _cfg: _ambiguous_register()
    )
    monkeypatch.setattr(analysis_route_tools, "_router_embedder", lambda _cfg: _FlatEmbedder())
    monkeypatch.setattr(analysis_route_tools, "_route_llm", lambda _cfg: chat)
    return await analysis_route(
        cast("asyncpg.Pool", None),
        config=config,
        question=_TIE_QUESTION,
        method_id=method_id,
    )


def _fallback_config(log_path: object, *, enabled: bool) -> dict[str, object]:
    return {
        "embedding": {"provider": "pgml"},
        "analysis": {
            "routing": {
                "llm_fallback_enabled": enabled,
                "fallback_log_path": str(log_path),
            }
        },
    }


_AXES_OK = {
    "task_type": "measurement",
    "comparison_structure": "none",
    "output_form": "statistic",
    "evidence_basis": "claims",
    "statistical_rigor": "descriptive",
    "cost_tier": "interactive",
}


def _answer(method_id: str, axes: dict[str, object] | None) -> str:
    payload: dict[str, object] = {"method_id": method_id}
    if axes is not None:
        payload["axes"] = axes
    return json.dumps(payload)


def _read_log(log_path: object) -> list[dict[str, object]]:
    text = Path(str(log_path)).read_text(encoding="utf-8")
    return [dict(json.loads(line)) for line in text.splitlines() if line.strip()]


async def test_default_config_carries_the_fallback_keys_off() -> None:
    """The fallback ships dormant: flag false, log path set, lockstep rows."""
    from corpus_kb.config import get_default_config

    routing = cast(
        "dict[str, object]",
        cast("dict[str, object]", get_default_config()["analysis"])["routing"],
    )
    assert routing["llm_fallback_enabled"] is False
    assert routing["fallback_log_path"] == "analysis_routing_fallbacks.jsonl"


async def test_flag_off_ambiguous_envelope_is_byte_identical_and_logs_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Flag off: same bytes as a config with no routing block; no log file."""
    log_path = tmp_path / "fallbacks.jsonl"
    register = _register(
        _record("05", "Cluster method", [_TIE_A, "Which cluster structure fits these records?"]),
        _record("01", "Codebook method", [_TIE_B, "Can a method help me code these transcripts?"]),
    )
    monkeypatch.setattr(analysis_route_tools, "load_method_register", lambda _cfg: register)
    monkeypatch.setattr(analysis_route_tools, "_router_embedder", lambda _cfg: _FlatEmbedder())
    with_log = json.dumps(
        await analysis_route(
            cast("asyncpg.Pool", None),
            config=_fallback_config(log_path, enabled=False),
            question=_TIE_A,
        ),
        sort_keys=True,
    )
    without_log = json.dumps(
        await analysis_route(cast("asyncpg.Pool", None), config={}, question=_TIE_A),
        sort_keys=True,
    )
    assert with_log == without_log
    assert not log_path.exists()


async def test_flag_on_llm_resolution_resolves_and_logs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A sane answer resolves the tie: note, axes, chips, one log row."""
    log_path = tmp_path / "fallbacks.jsonl"
    chat = _ScriptedChat(content=_answer("01", _AXES_OK))
    result = await _ambiguous_run(monkeypatch, chat, _fallback_config(log_path, enabled=True))
    assert result["status"] == "resolved"
    assert "interpretations" not in result
    intent = cast("dict[str, object]", result["intent"])
    assert intent["status"] == "resolved"
    assert intent["note"] == "llm_fallback"
    assert intent["method_id"] == "01"
    axes = cast("dict[str, object]", intent["axes"])
    assert axes["task_type"] == "measurement"
    assert result["suggestions"] == ["Which first codes anchor the book?"]
    rows = _read_log(log_path)
    assert len(rows) == 1
    row = rows[0]
    assert set(row) == {"ts", "question", "attempt", "outcome", "chosen_method_id"}
    assert row["outcome"] == "resolved"
    assert row["chosen_method_id"] == "01"
    assert row["question"] == _TIE_QUESTION
    assert row["attempt"] == chat.script


async def test_flag_on_unknown_method_id_fails_and_logs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An id outside the register fails the sanity gate and stays ambiguous."""
    log_path = tmp_path / "fallbacks.jsonl"
    chat = _ScriptedChat(content=_answer("99", None))
    result = await _ambiguous_run(monkeypatch, chat, _fallback_config(log_path, enabled=True))
    assert result["status"] == "ambiguous"
    intent = cast("dict[str, object]", result["intent"])
    assert intent["note"] is None
    rows = _read_log(log_path)
    assert rows[0]["outcome"] == "failed"
    assert rows[0]["chosen_method_id"] == "99"


async def test_flag_on_axis_outside_vocabulary_fails_and_logs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An axis value outside its tuple fails the gate; the tuples are the check."""
    log_path = tmp_path / "fallbacks.jsonl"
    chat = _ScriptedChat(content=_answer("01", {"task_type": "tarot"}))
    result = await _ambiguous_run(monkeypatch, chat, _fallback_config(log_path, enabled=True))
    assert result["status"] == "ambiguous"
    rows = _read_log(log_path)
    assert rows[0]["outcome"] == "failed"
    assert rows[0]["chosen_method_id"] == "01"


async def test_flag_on_seam_error_fails_and_logs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A down model is a logged failed attempt, never a raise."""
    log_path = tmp_path / "fallbacks.jsonl"
    chat = _ScriptedChat(error="Ollama request failed: connection refused")
    result = await _ambiguous_run(monkeypatch, chat, _fallback_config(log_path, enabled=True))
    assert result["status"] == "ambiguous"
    rows = _read_log(log_path)
    assert rows[0]["outcome"] == "failed"
    assert "chosen_method_id" not in rows[0]
    assert rows[0]["attempt"] == ""


async def test_flag_on_unparseable_answer_fails_and_logs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Prose instead of JSON is a failed attempt; the tie stays."""
    log_path = tmp_path / "fallbacks.jsonl"
    chat = _ScriptedChat(content="I cannot answer that question.")
    result = await _ambiguous_run(monkeypatch, chat, _fallback_config(log_path, enabled=True))
    assert result["status"] == "ambiguous"
    rows = _read_log(log_path)
    assert rows[0]["outcome"] == "failed"


async def test_flag_on_fenced_json_answer_parses(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A fenced JSON answer parses: the slice from brace to brace."""
    log_path = tmp_path / "fallbacks.jsonl"
    fenced = "```json\n" + _answer("01", {"task_type": "representation"}) + "\n```"
    chat = _ScriptedChat(content=fenced)
    result = await _ambiguous_run(monkeypatch, chat, _fallback_config(log_path, enabled=True))
    assert result["status"] == "resolved"
    intent = cast("dict[str, object]", result["intent"])
    assert intent["method_id"] == "01"


async def test_flag_on_resolved_route_never_calls_the_llm(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A decided route spends no LLM call and writes no log row."""
    log_path = tmp_path / "fallbacks.jsonl"
    chat = _ScriptedChat(content=_answer("01", None))
    register = _register(
        _record("05", "Cluster method", [_TIE_A, "Which cluster structure fits these records?"]),
    )
    monkeypatch.setattr(analysis_route_tools, "load_method_register", lambda _cfg: register)
    monkeypatch.setattr(analysis_route_tools, "_router_embedder", lambda _cfg: _SplitEmbedder())
    monkeypatch.setattr(analysis_route_tools, "_route_llm", lambda _cfg: chat)
    result = await analysis_route(
        cast("asyncpg.Pool", None), config=_fallback_config(log_path, enabled=True), question=_TIE_A
    )
    assert result["status"] == "resolved"
    assert chat.calls == 0
    assert not log_path.exists()


async def test_flag_on_override_skips_the_fallback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The analyst's choice wins before the LLM is ever asked."""
    log_path = tmp_path / "fallbacks.jsonl"
    chat = _ScriptedChat(content=_answer("01", None))
    result = await _ambiguous_run(
        monkeypatch, chat, _fallback_config(log_path, enabled=True), method_id="01"
    )
    intent = cast("dict[str, object]", result["intent"])
    assert intent["note"] == "expert_override"
    assert chat.calls == 0
    assert not log_path.exists()


async def test_flag_on_unknown_axis_names_log_as_ignored(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Junk axis NAMES ride the audit row, never the intent; gate unchanged.

    An answer may carry keys outside the six tuples: they are dropped
    from the intent and their names land in the log's ``ignored_axes``
    (an omitted axis and junk the model sent stop looking alike).
    """
    log_path = tmp_path / "fallbacks.jsonl"
    axes = dict(_AXES_OK)
    axes["spiritual_rank"] = "high"
    chat = _ScriptedChat(content=_answer("01", axes))
    result = await _ambiguous_run(monkeypatch, chat, _fallback_config(log_path, enabled=True))
    assert result["status"] == "resolved"
    intent = cast("dict[str, object]", result["intent"])
    axes_out = cast("dict[str, object]", intent["axes"])
    assert axes_out["task_type"] == "measurement"
    assert "spiritual_rank" not in axes_out
    rows = _read_log(log_path)
    assert rows[0]["outcome"] == "resolved"
    assert rows[0]["ignored_axes"] == ["spiritual_rank"]


async def test_degraded_tie_resolved_by_the_llm_keeps_the_degraded_status(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The status describes the run; the note describes the decision.

    A zero-vector embedder degrades the run, yet a passing LLM attempt
    still resolves the intent: envelope status stays ``degraded``,
    ``intent.note == "llm_fallback"``, and the chips ride the resolved
    record (full functionality rides intent resolution, W4 precedent).
    """
    log_path = tmp_path / "fallbacks.jsonl"
    chat = _ScriptedChat(content=_answer("01", _AXES_OK))
    monkeypatch.setattr(
        analysis_route_tools, "load_method_register", lambda _cfg: _ambiguous_register()
    )
    monkeypatch.setattr(analysis_route_tools, "_router_embedder", lambda _cfg: _ZeroEmbedder())
    monkeypatch.setattr(analysis_route_tools, "_route_llm", lambda _cfg: chat)
    result = await analysis_route(
        cast("asyncpg.Pool", None),
        config=_fallback_config(log_path, enabled=True),
        question=_TIE_QUESTION,
    )
    assert result["status"] == "degraded"
    intent = cast("dict[str, object]", result["intent"])
    assert intent["status"] == "resolved"
    assert intent["note"] == "llm_fallback"
    assert intent["method_id"] == "01"
    assert result["suggestions"] == ["Which first codes anchor the book?"]
    assert "interpretations" not in result
    rows = _read_log(log_path)
    assert rows[0]["outcome"] == "resolved"
    assert "ignored_axes" not in rows[0]
