"""Transcript ingest specs shared by the research ingestion path (todo-11 (a)).

Transcripts REUSE the legacy `Document` aggregate (source_type='transcript');
the TurnsParsed / ExchangesLinked events live on that aggregate (see
domain/aggregates.py) — a second aggregate class with a bare-name `Ingested`
event would double-project through DocumentsProjection's name dispatch.

Turn text is written ONCE to the immutable content-addressed store
(research_transcript_text) at ingest time; events carry text_sha256
references plus speaker/role metadata, never full text. Batch sizes are
capped at the aggregate methods.
"""

from __future__ import annotations

import hashlib


def normalize_turn_text(text: str) -> str:
    """Normalize turn text before hashing (ingest-side contract)."""
    return " ".join(text.split()).strip()


def text_sha256(normalized_text: str) -> str:
    """Content address of normalized turn text (research_transcript_text PK)."""
    return hashlib.sha256(normalized_text.encode("utf-8")).hexdigest()


class TurnSpec:
    """One speaker turn as parsed at ingest time (pre-hash)."""

    __slots__ = (
        "is_codable",
        "media_type",
        "role",
        "role_in_exchange",
        "seq",
        "speaker",
        "t_end",
        "t_start",
        "text",
        "turn_type",
    )

    def __init__(
        self,
        seq: int,
        speaker: str,
        text: str,
        role: str = "unknown",
        role_in_exchange: str = "other",
        t_start: float | None = None,
        t_end: float | None = None,
        media_type: str = "text",
        is_codable: bool = True,
        turn_type: str = "answer",
    ) -> None:
        self.seq = seq
        self.speaker = speaker
        self.text = text
        self.role = role
        self.role_in_exchange = role_in_exchange
        self.t_start = t_start
        self.t_end = t_end
        self.media_type = media_type
        self.is_codable = is_codable
        self.turn_type = turn_type

    def to_payload(self) -> dict[str, object]:
        normalized = normalize_turn_text(self.text)
        payload: dict[str, object] = {
            "seq": self.seq,
            "text_sha256": text_sha256(normalized),
            "speaker": self.speaker,
            "role": self.role,
            "role_in_exchange": self.role_in_exchange,
            "turn_type": self.turn_type,
            "is_codable": self.is_codable,
            "media_type": self.media_type,
        }
        if self.t_start is not None:
            payload["t_start"] = self.t_start
        if self.t_end is not None:
            payload["t_end"] = self.t_end
        return payload


class ExchangeSpec:
    """One moderator-question -> participant-answer link (pre-hash)."""

    __slots__ = (
        "a_unit_seqs",
        "link_confidence",
        "link_method",
        "link_score",
        "q_unit_seqs",
        "seq",
        "stance",
        "term_origin",
        "topic_id",
    )

    def __init__(
        self,
        seq: int,
        q_unit_seqs: list[int],
        a_unit_seqs: list[int],
        link_method: str = "adjacency",
        link_confidence: str = "high",
        link_score: float | None = None,
        stance: str | None = None,
        term_origin: str | None = None,
        topic_id: int | None = None,
    ) -> None:
        self.seq = seq
        self.q_unit_seqs = q_unit_seqs
        self.a_unit_seqs = a_unit_seqs
        self.link_method = link_method
        self.link_confidence = link_confidence
        self.link_score = link_score
        self.stance = stance
        self.term_origin = term_origin
        self.topic_id = topic_id

    def to_payload(self) -> dict[str, object]:
        payload: dict[str, object] = {
            "seq": self.seq,
            "q_unit_seqs": self.q_unit_seqs,
            "a_unit_seqs": self.a_unit_seqs,
            "link_method": self.link_method,
            "link_confidence": self.link_confidence,
        }
        if self.link_score is not None:
            payload["link_score"] = self.link_score
        if self.stance is not None:
            payload["stance"] = self.stance
        if self.term_origin is not None:
            payload["term_origin"] = self.term_origin
        if self.topic_id is not None:
            payload["topic_id"] = self.topic_id
        return payload
