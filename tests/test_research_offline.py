"""Offline tests for the research ingestion semantics (todo-11 (d)/(e)).

No Postgres, no Ollama: role heuristic, turn typing, adjacency/probe
absorption, cross_ref decision, stance/term_origin, payload caps, and G0
parse checks on the fixture text. These run in CI; DB-bound E2E is
requires_postgres.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from corpus_kb.domain.aggregates import Document
from corpus_kb.domain.transcript import TurnSpec, normalize_turn_text, text_sha256
from corpus_kb.research.linking import link_exchanges
from corpus_kb.research.roles import map_roles, speaker_role_basis
from corpus_kb.research.transcript_parser import parse_transcript

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "research"


def _turns(*pairs: tuple[str, str]) -> list[TurnSpec]:
    return [TurnSpec(seq=i, speaker=s, text=t) for i, (s, t) in enumerate(pairs)]


def test_role_heuristic_moderator_highest_question_ratio_and_first():
    turns = _turns(
        ("Interviewer", "How did onboarding go?"),
        ("Dana", "Rough, honestly."),
        ("Interviewer", "Why was that?"),
        ("Dana", "Stale docs."),
    )
    assert map_roles(turns) == "auto"
    assert [t.role for t in turns] == ["moderator", "participant", "moderator", "participant"]
    assert "question_ratio" in speaker_role_basis(turns)


def test_group_never_auto_confirmed():
    turns = _turns(
        ("Facilitator", "Welcome everyone, shall we begin?"),
        ("A", "Hello."),
        ("B", "Hi."),
        ("C", "Present."),
    )
    assert map_roles(turns) == "group_review"
    assert all(t.role == "unknown" for t in turns)


def test_turn_typing_moderator_not_codable():
    turns = _turns(
        ("M", "How did onboarding go?"),
        ("P", "Rough."),
        ("M", "Moving on to support - what happened?"),
        ("P", "Fast response."),
    )
    map_roles(turns)
    link_exchanges(turns)
    assert [t.is_codable for t in turns] == [False, True, False, True]
    assert turns[0].role_in_exchange == "main_question"
    assert turns[2].role_in_exchange == "transition"


def test_adjacency_absorbs_probes():
    turns = _turns(
        ("M", "How did onboarding go?"),
        ("P", "Rough."),
        ("M", "Can you say more?"),
        ("P", "Docs were stale."),
    )
    map_roles(turns)
    exchanges = link_exchanges(turns)
    assert len(exchanges) == 1
    assert exchanges[0].q_unit_seqs == [0, 2]
    assert exchanges[0].a_unit_seqs == [1, 3]
    assert exchanges[0].link_method == "adjacency"


def test_cross_ref_when_similarity_to_earlier_question_wins():
    def embed(text: str) -> list[float]:
        vocabulary = ["pricing", "onboarding", "trust", "support"]
        vector = [1.0 if v in text.lower() else 0.0 for v in vocabulary]
        return vector or [0.1, 0.1, 0.1, 0.1]

    turns = _turns(
        ("M", "What do you think about pricing and trust?"),
        ("P", "Pricing seems fair and trust is high."),
        ("M", "Any other feedback about support?"),
        ("P", "Support was slow."),
        ("M", "Thanks."),
        ("P", "Going back to what you asked about pricing and trust: still fair."),
    )
    map_roles(turns)
    exchanges = link_exchanges(turns, embed=embed)
    cross = [e for e in exchanges if e.link_method == "cross_ref"]
    assert cross, "expected a cross_ref link"
    assert cross[0].link_confidence in ("medium", "low")


def test_stance_and_term_origin():
    turns = _turns(
        ("M", "Do you trust the onboarding now?"),
        ("P", "Yes, mostly because of the support."),
        ("M", "Was the old vendor faster?"),
        ("P", "No, not really."),
        ("M", "Why stay onboarding with us?"),
        ("P", "Eh, whatever."),
    )
    map_roles(turns)
    exchanges = link_exchanges(turns)
    stances = [e.stance for e in exchanges]
    assert stances[0] == "affirm"
    assert stances[1] == "deny"
    assert stances[2] == "deflect"
    assert exchanges[0].term_origin in ("moderator", "both")


def test_turn_payload_caps():
    doc = Document(tenant_id=__import__("uuid").uuid4(), source="x")
    with pytest.raises(ValueError):
        doc.add_turn_batch(
            tenant_id=doc.tenant_id,
            turns=[{"seq": i, "text_sha256": "a" * 64, "speaker": "M"} for i in range(51)],
        )
    with pytest.raises(ValueError):
        doc.add_turn_batch(tenant_id=doc.tenant_id, turns=[{"seq": 0, "speaker": "M"}])


def test_events_carry_tenant_contract():
    import uuid

    tenant = uuid.uuid4()
    doc = Document(tenant_id=tenant, source="x")
    doc.add_turn_batch(
        tenant_id=tenant,
        turns=[{"seq": 0, "text_sha256": "a" * 64, "speaker": "M"}],
    )
    pending = doc.collect_events()
    assert pending, "no pending events"
    turn_event = pending[-1]
    assert getattr(turn_event, "tenant_id", None) == tenant


def test_fixture_g0_parse_and_roles():
    parsed = parse_transcript(FIXTURES / "exchange_fixture_interview.md")
    assert parsed.parse_quality == "verified"
    assert len(parsed.turns) == 11
    assert all(t.speaker for t in parsed.turns), "G0: unlabeled turns found"
    result = map_roles(parsed.turns)
    assert result == "auto"
    roles = {t.speaker: t.role for t in parsed.turns}
    assert roles["Moderator"] == "moderator"
    assert roles["P1 Dana"] == "participant"


def test_normalize_and_hash_stable():
    assert normalize_turn_text("  a\n\n  b  ") == "a b"
    assert text_sha256(normalize_turn_text("a b")) == text_sha256("a b")


def test_parse_quality_unknown_format_flagged(tmp_path):
    weird = tmp_path / "weird.txt"
    weird.write_text("no speaker labels at all just prose\n", encoding="utf-8")
    parsed = parse_transcript(weird)
    assert parsed.parse_quality == "unverified"
