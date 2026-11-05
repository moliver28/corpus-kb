"""Tests for the wave-2 question intent schema and the deterministic router.

Pins the Task 2 Review Focus items: a row without ``question_phrases`` is
skipped from scoring, a repeat run is byte-identical, a degenerate register
answers ``insufficient``, degraded mode never fakes confidence, and a
non-string question raises ``TypeError``. Embedding-mode tests use a
module-local deterministic embedder; no test here needs Ollama.
"""

from __future__ import annotations

import json
from dataclasses import asdict
from typing import cast

import pytest

from corpus_kb.analysis.method_register import MethodRegister
from corpus_kb.analysis.method_register_routing import ROUTING_VOCAB_FIELDS
from corpus_kb.analysis.method_register_rubric import rank_methods
from corpus_kb.analysis.question_intent import ROUTING_OUTCOMES, QuestionIntent
from corpus_kb.analysis.question_router import RouteResult, RoutingTrace, route_question

# --- module-local deterministic embedders (fixed structure, no Ollama) -----


EMBED_DIMENSIONS = 8
KEYWORD_AXES = {"codebook": 0, "cluster": 1, "topic": 2, "trend": 3}


class TopicEmbedder:
    """Deterministic fake: known keywords light fixed axes, the rest is noise.

    Same text, same vector, every process and run. Texts sharing a keyword
    share a direction, so cosine to a phrase centroid behaves like a real
    embedding model without any network call.
    """

    dimensions = EMBED_DIMENSIONS

    def embed(self, text: str) -> list[float]:
        noise = [((index * 7 + len(text)) % 5) * 0.01 for index in range(EMBED_DIMENSIONS)]
        folded = text.casefold()
        for word, axis in KEYWORD_AXES.items():
            if word in folded:
                noise[axis] += 1.0
        return noise


class ZeroEmbedder:
    """The degraded-embedder signal: every vector is the zero vector."""

    dimensions = EMBED_DIMENSIONS

    def embed(self, text: str) -> list[float]:
        return [0.0] * EMBED_DIMENSIONS


# --- hand-built registers (subsets stay valid; the loader owns the ledger) -


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


def _register(*rows: dict[str, object], version: str = "1.1.0") -> MethodRegister:
    return MethodRegister.model_validate(
        {
            "register_version": version,
            "source_document": {"title": "Test document", "path_note": "Test rows only."},
            "selection_rules": ["Match the required output."],
            "adjacent_candidates": "None.",
            "methods": list(rows),
        }
    )


CLUSTER_PHRASES = [
    "How do I cluster my documents by theme?",
    "Which cluster structure fits these records?",
]
CODEBOOK_PHRASES = [
    "How do I build a codebook for my interviews?",
    "Can a method help me code these transcripts?",
]


def _cluster_register() -> MethodRegister:
    return _register(_record("05", "Cluster method", CLUSTER_PHRASES))


# --------------------------------------------------------------------------
# QuestionIntent schema
# --------------------------------------------------------------------------


class TestIntentSchema:
    def test_outcomes_tuple_is_pinned(self) -> None:
        assert ROUTING_OUTCOMES == ("resolved", "ambiguous", "insufficient")

    def test_axes_default_to_six_none_values(self) -> None:
        intent = QuestionIntent(question="q", status="insufficient")
        assert set(intent.axes) == set(ROUTING_VOCAB_FIELDS)
        assert all(value is None for value in intent.axes.values())

    def test_unknown_status_is_rejected(self) -> None:
        with pytest.raises(ValueError, match="status"):
            QuestionIntent(question="q", status="degraded")

    def test_axes_passthrough_without_vocab_check(self) -> None:
        """Axis validation lives in the register; the intent accepts values as-is.

        The envelope still carries all six pinned keys: a partial axes dict
        merges over the defaults when ``to_envelope`` runs.
        """
        intent = QuestionIntent(
            question="q",
            status="resolved",
            confidence=1.0,
            method_id="05",
            axes={"task_type": "not_a_vocab_value"},
        )
        envelope_axes = intent.to_envelope()["axes"]
        assert envelope_axes["task_type"] == "not_a_vocab_value"
        assert set(envelope_axes) == set(ROUTING_VOCAB_FIELDS)
        assert all(value is None for key, value in envelope_axes.items() if key != "task_type")

    def test_envelope_field_order_is_stable(self) -> None:
        envelope = QuestionIntent(question="q", status="resolved").to_envelope()
        assert list(envelope) == [
            "question",
            "status",
            "confidence",
            "method_id",
            "axes",
            "ranked_method_ids",
            "readings",
            "note",
        ]


# --------------------------------------------------------------------------
# Phrase path (embedder=None -> degraded phrase scoring)
# --------------------------------------------------------------------------


class TestPhrasePath:
    def test_single_candidate_resolves_when_score_clears_threshold(self) -> None:
        """Both phrases of the only row sit in the question: score 1.0 resolves."""
        register = _cluster_register()
        question = (
            "How do I cluster my documents by theme? Also which cluster structure fits these "
            "records?"
        )
        result = route_question(question, register, None, None)
        assert isinstance(result, RouteResult)
        assert result.status == "resolved"
        assert result.degraded is True
        assert result.intent.method_id == "05"
        assert result.intent.confidence == pytest.approx(1.0)
        assert result.intent.note == "degraded"
        assert result.trace.mode == "phrase"

    def test_clear_winner_resolves_via_threshold_and_margin(self) -> None:
        phrases_b = [
            *CODEBOOK_PHRASES,
            "What topics drive this document set today?",
            "Which topic words label these records well?",
        ]
        register = _register(
            _record("05", "Cluster method", CLUSTER_PHRASES),
            _record("06", "Codebook assist", phrases_b),
        )
        question = (
            "How do I cluster my documents by theme? Which cluster structure fits these records? "
            "How do I build a codebook for my interviews? Can a method help me code these "
            "transcripts?"
        )
        result = route_question(question, register, None, None)
        assert result.status == "resolved"
        assert result.intent.method_id == "05"
        scores = {row["method_id"]: row["score"] for row in result.trace.scores}
        assert scores["05"] == pytest.approx(1.0)
        assert scores["06"] == pytest.approx(0.5)

    def test_tied_scores_stay_ambiguous_with_readings(self) -> None:
        """Top clears the threshold but the margin does not: ambiguous, top-3 readings."""
        register = _register(
            _record("01", "Cluster method", ["How do I cluster my documents by theme?"]),
            _record("02", "Grouping method", ["How do I cluster my documents by theme?"]),
            _record("03", "Sorting method", ["How do I cluster my documents by theme?"]),
        )
        result = route_question("How do I cluster my documents by theme?", register, None, None)
        assert result.status == "ambiguous"
        assert result.intent.method_id is None
        assert result.intent.note == "degraded"
        assert result.degraded is True
        assert result.intent.ranked_method_ids == ["01", "02", "03"]
        assert [reading["method_id"] for reading in result.intent.readings] == ["01", "02", "03"]
        assert all(
            reading["why"] == "How do I cluster my documents by theme?"
            for reading in result.intent.readings
        )
        assert all(reading["method_name"] for reading in result.intent.readings)

    def test_single_candidate_below_threshold_stays_ambiguous(self) -> None:
        """One member in C but score under the threshold: ambiguous, one reading."""
        register = _register(_record("05", "Cluster method", CLUSTER_PHRASES))
        result = route_question("Which cluster structure fits these records?", register, None, None)
        assert result.status == "ambiguous"
        assert result.intent.method_id is None
        assert len(result.intent.readings) == 1
        assert result.intent.readings[0]["why"] == "Which cluster structure fits these records?"

    def test_fallback_pool_scores_all_phrase_rows_with_method_name_whys(self) -> None:
        register = _register(
            _record("02", "Cluster method", CLUSTER_PHRASES),
            _record("01", "Codebook assist", CODEBOOK_PHRASES),
        )
        result = route_question(
            "Tell me what matters in this corpus, please.", register, None, None
        )
        assert result.status == "ambiguous"
        assert result.intent.ranked_method_ids == ["01", "02"]
        scores = [row["score"] for row in result.trace.scores]
        assert scores == [0.0, 0.0]
        assert [reading["why"] for reading in result.intent.readings] == [
            "Codebook assist",
            "Cluster method",
        ]
        assert [reading["method_id"] for reading in result.intent.readings] == ["01", "02"]

    def test_degenerate_register_answers_insufficient(self) -> None:
        register = _register(_record("05", "Manual method"), _record("06", "Other method"))
        result = route_question("How do I cluster my documents by theme?", register, None, None)
        assert result.status == "insufficient"
        assert result.intent.status == "insufficient"
        assert result.intent.method_id is None
        assert result.intent.readings == []
        assert result.intent.ranked_method_ids == []
        assert result.trace.scores == []
        assert result.intent.confidence == 0.0
        assert result.trace.mode == "phrase"

    def test_insufficient_with_an_embedder_reports_the_phrase_mode(self) -> None:
        """Nothing was scored, so the trace must not claim embedding mode."""
        register = _register(_record("05", "Manual method"), _record("06", "Other method"))
        result = route_question(
            "How do I cluster my documents by theme?", register, ZeroEmbedder(), None
        )
        assert result.status == "insufficient"
        assert result.trace.mode == "phrase"
        assert result.trace.scores == []

    def test_row_without_question_phrases_is_skipped_from_scoring(self) -> None:
        """Review-Focus #4: a phrase-less row never crashes and never scores."""
        register = _register(
            _record("05", "Cluster method", CLUSTER_PHRASES),
            _record("06", "Phrase-less method"),
        )
        result = route_question("Which cluster structure fits these records?", register, None, None)
        assert result.status == "ambiguous"
        assert result.intent.ranked_method_ids == ["05"]
        assert all(row["method_id"] == "05" for row in result.trace.scores)
        assert [reading["method_id"] for reading in result.intent.readings] == ["05"]

    def test_short_question_is_handled_gracefully(self) -> None:
        """Below 8 characters the op layer raises later; here it must not crash."""
        register = _register(
            _record("05", "Cluster method", CLUSTER_PHRASES),
            _record("06", "Codebook assist", CODEBOOK_PHRASES),
        )
        for question in ("hi", "", "   "):
            result = route_question(question, register, None, None)
            assert result.status in ROUTING_OUTCOMES

    def test_non_string_question_raises_type_error(self) -> None:
        register = _cluster_register()
        with pytest.raises(TypeError, match="question"):
            route_question(cast(str, 123), register, None, None)


# --------------------------------------------------------------------------
# Degraded embedding handling (zero vectors never fake confidence)
# --------------------------------------------------------------------------


class TestDegradedEmbedding:
    def test_zero_vector_question_falls_back_to_phrase_scoring(self) -> None:
        """A zero-vector question never fakes embedding confidence."""
        register = _register(_record("05", "Cluster method", CLUSTER_PHRASES))
        question = (
            "How do I cluster my documents by theme? Which cluster structure fits these records?"
        )
        result = route_question(question, register, ZeroEmbedder(), None)
        assert result.status == "resolved"
        assert result.degraded is True
        assert result.trace.mode == "phrase"
        assert result.intent.note == "degraded"
        assert result.intent.confidence == pytest.approx(1.0)
        assert result.intent.method_id == "05"

    def test_degraded_hit_share_uses_the_row_phrase_count(self) -> None:
        """One hit across two authored phrases scores 0.5, not 1.0."""
        register = _cluster_register()
        result = route_question("Which cluster structure fits these records?", register, None, None)
        assert result.trace.scores[0]["score"] == pytest.approx(0.5)

    def test_margin_boundary_passes_at_exact_arithmetic_equality(self) -> None:
        """Seven hits versus six hits out of ten phrases: the exact gap is 0.10.

        Float subtraction yields 0.09999999999999998, so the decision must
        hold the comparison to within a small tolerance (review round 1).
        """
        phrases_a = [
            f"Cluster question number {index} for the suite here?" for index in range(1, 11)
        ]
        phrases_b = [
            f"Codebook question number {index} for the suite here?" for index in range(1, 11)
        ]
        register = _register(
            _record("05", "Cluster method", phrases_a),
            _record("06", "Codebook assist", phrases_b),
        )
        question = " ".join(phrases_a[:7] + phrases_b[:6])
        result = route_question(question, register, None, None)
        assert result.trace.margin == 0.10
        scores = {row["method_id"]: row["score"] for row in result.trace.scores}
        assert scores["05"] == pytest.approx(0.7)
        assert scores["06"] == pytest.approx(0.6)
        assert result.status == "resolved"
        assert result.intent.method_id == "05"


# --------------------------------------------------------------------------
# Embedding mode (module-local deterministic embedder)
# --------------------------------------------------------------------------


class TestEmbeddingMode:
    def test_embedding_scores_resolve_through_the_fallback_pool(self) -> None:
        """No verbatim phrase hit: C is the fallback pool, cosine picks the branch."""
        register = _register(
            _record("05", "Cluster method", CLUSTER_PHRASES),
            _record("06", "Codebook assist", CODEBOOK_PHRASES),
        )
        question = "What cluster count fits my document collection this month?"
        result = route_question(question, register, TopicEmbedder(), None)
        assert result.degraded is False
        assert result.trace.mode == "embedding"
        assert result.intent.note is None
        assert result.status == "resolved"
        assert result.intent.method_id == "05"
        assert result.intent.confidence > 0.55
        scores = {row["method_id"]: row["score"] for row in result.trace.scores}
        assert scores["05"] > scores["06"]

    def test_repeat_run_is_byte_identical_with_a_warm_cache(self) -> None:
        register = _register(
            _record("05", "Cluster method", CLUSTER_PHRASES),
            _record("06", "Codebook assist", CODEBOOK_PHRASES),
        )
        embedder = TopicEmbedder()
        question = "What cluster count fits my document collection this month?"
        first = route_question(question, register, embedder, None)
        second = route_question(question, register, embedder, None)
        first_blob = json.dumps(
            {"intent": first.intent.to_envelope(), "trace": asdict(first.trace)}
        )
        second_blob = json.dumps(
            {"intent": second.intent.to_envelope(), "trace": asdict(second.trace)}
        )
        assert first_blob == second_blob

    def test_repeat_run_is_byte_identical_without_an_embedder(self) -> None:
        register = _register(
            _record("05", "Cluster method", CLUSTER_PHRASES),
            _record("06", "Codebook assist", CODEBOOK_PHRASES),
        )
        question = "How do I cluster my documents by theme?"
        first = route_question(question, register, None, None)
        second = route_question(question, register, None, None)
        assert json.dumps(first.intent.to_envelope()) == json.dumps(second.intent.to_envelope())
        assert asdict(first.trace) == asdict(second.trace)

    def test_centroid_cache_separates_registers_sharing_a_version(self) -> None:
        """Two registers with the same version must not share cached centroids."""
        first_register = _register(
            _record("05", "Cluster method", ["I want to cluster my document set now"])
        )
        second_register = _register(
            _record("05", "Cluster method", ["I want a codebook for my study now"])
        )
        question = "How do I cluster my document set this week?"
        first = route_question(question, first_register, TopicEmbedder(), None)
        second = route_question(question, second_register, TopicEmbedder(), None)
        assert first.trace.scores[0]["score"] > second.trace.scores[0]["score"]


# --------------------------------------------------------------------------
# Settings block (analysis.routing)
# --------------------------------------------------------------------------


class TestSettings:
    def _five_phrase_register(self) -> MethodRegister:
        phrases = [
            *CLUSTER_PHRASES,
            "What topics drive this document set today?",
            "Which topic words label these records well?",
            "Where do these records group together naturally?",
        ]
        return _register(_record("05", "Cluster method", phrases))

    def test_missing_settings_block_uses_pinned_defaults(self) -> None:
        register = _register(_record("05", "Cluster method", CLUSTER_PHRASES))
        result = route_question("How do I cluster my documents by theme?", register, None, None)
        assert result.trace.threshold == 0.55
        assert result.trace.margin == 0.10

    def test_threshold_override_moves_the_decision(self) -> None:
        """Three hits across five phrases score 0.6: default resolves, 0.8 does not."""
        register = self._five_phrase_register()
        question = (
            "How do I cluster my documents by theme? Which cluster structure fits these records? "
            "What topics drive this document set today?"
        )
        default_result = route_question(question, register, None, None)
        assert default_result.status == "resolved"
        strict_result = route_question(
            question, register, None, {"confidence_threshold": 0.8, "margin": 0.10}
        )
        assert strict_result.status == "ambiguous"
        assert strict_result.trace.threshold == 0.8

    def test_margin_override_moves_the_decision(self) -> None:
        """Top 1.0 vs second 0.8: default margin 0.10 resolves, 0.25 does not."""
        register = _register(
            _record("05", "Cluster method", ["How do I cluster my documents by theme?"]),
            _record(
                "06",
                "Codebook assist",
                [
                    *CODEBOOK_PHRASES,
                    "What topics drive this document set today?",
                    "Where do these records group together naturally?",
                    "Which records look alike to you here?",
                ],
            ),
        )
        question = (
            "How do I cluster my documents by theme? How do I build a codebook for my interviews? "
            "Can a method help me code these transcripts? What topics drive this document set "
            "today? Where do these records group together naturally?"
        )
        default_result = route_question(question, register, None, None)
        assert default_result.status == "resolved"
        strict_result = route_question(
            question, register, None, {"confidence_threshold": 0.55, "margin": 0.25}
        )
        assert strict_result.status == "ambiguous"
        assert strict_result.trace.margin == 0.25

    def test_unusable_setting_value_falls_back_to_the_default(self) -> None:
        """A non-numeric knob value falls back; the route never crashes."""
        register = _cluster_register()
        result = route_question(
            "How do I cluster my documents by theme?",
            register,
            None,
            {"confidence_threshold": 0.55, "margin": "tight"},
        )
        assert result.trace.margin == 0.10
        assert result.trace.threshold == 0.55
        assert result.status in ROUTING_OUTCOMES


# --------------------------------------------------------------------------
# Trace shape and the rubric join
# --------------------------------------------------------------------------


class TestTraceAndRubricJoin:
    def test_trace_scores_cap_at_ten_rows_in_pinned_order(self) -> None:
        rows = [
            _record(f"{index:02d}", f"Method {index:02d}", ["How do I cluster my records now?"])
            for index in range(1, 13)
        ]
        register = _register(*rows)
        result = route_question(
            "Tell me what matters in this corpus, please.", register, None, None
        )
        assert len(result.trace.scores) == 10
        assert [row["method_id"] for row in result.trace.scores] == [
            f"{index:02d}" for index in range(1, 11)
        ]
        assert all(set(row) == {"method_id", "score"} for row in result.trace.scores)

    def test_trace_scores_order_by_score_desc_then_id_asc(self) -> None:
        register = _register(
            _record("02", "Two of two", CLUSTER_PHRASES),
            _record("01", "One of two", CLUSTER_PHRASES + CODEBOOK_PHRASES),
        )
        question = (
            "How do I cluster my documents by theme? Which cluster structure fits these records?"
        )
        result = route_question(question, register, None, None)
        assert [(row["method_id"], row["score"]) for row in result.trace.scores] == [
            ("02", pytest.approx(1.0)),
            ("01", pytest.approx(0.5)),
        ]

    def test_ranked_rides_the_one_rubric_inside_the_candidate_branch(self) -> None:
        register = _register(
            _record("06", "Codebook assist", CODEBOOK_PHRASES),
            _record("05", "Cluster method", CLUSTER_PHRASES),
        )
        question = (
            "How do I cluster my documents by theme? Which cluster structure fits these records? "
            "How do I build a codebook for my interviews?"
        )
        result = route_question(question, register, None, None)
        expected_ids = [rec.id for rec in rank_methods(register) if rec.id in {"05", "06"}][:5]
        assert [rec.id for rec in result.ranked] == expected_ids
        assert len(result.ranked) <= 5

    def test_route_result_mirrors_intent_status(self) -> None:
        register = _register(_record("05", "Manual method"))
        result = route_question("How do I cluster my documents by theme?", register, None, None)
        assert result.status == result.intent.status == "insufficient"
        assert isinstance(result.trace, RoutingTrace)
