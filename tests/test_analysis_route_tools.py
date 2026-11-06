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
GOLDEN_JSON = """{"intent": {"axes": {"comparison_structure": null, "cost_tier": null, "evidence_basis": null, "output_form": null, "statistical_rigor": null, "task_type": null}, "confidence": 0.6666666666666666, "method_id": "29", "note": "degraded", "question": "find duplicate or paraphrased records before analysis. which records say nearly the same thing? i want to merge near duplicates without losing speakers. detect repeated submissions and rephrased copies.", "ranked_method_ids": ["29"], "readings": [], "status": "resolved"}, "ranked": [{"method_id": "29", "name": "Near-duplicate and paraphrase detection", "run_hint": {"op": "duplicates_screen", "params": {}}, "why": "Find duplicate or paraphrased records before analysis."}], "status": "degraded", "suggestions": ["Which near duplicates are genuinely separate speakers?", "How do thresholds split review from merge?", "What paraphrases survived the exact-match pass?"], "trace": {"margin": 0.1, "mode": "phrase", "scores": [{"method_id": "29", "score": 0.6666666666666666}], "threshold": 0.55}}"""  # noqa: E501


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


def _record(
    id_: str,
    name: str,
    phrases: list[str] | None = None,
    suggests: list[str] | None = None,
) -> dict[str, object]:
    """One minimal valid method row; ``None`` drops the routing field."""
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
    if suggests is not None:
        row["suggests"] = suggests
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
    assert set(result) == {"status", "intent", "ranked", "trace", "suggestions"}
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
