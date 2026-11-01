"""Deterministic statement atomization (Living Corpus W2).

One chunk becomes zero or more statement atoms: sentences classified by
the claim-template catalogue, anchored to the extraction-stage entities
whose spans fall inside them. No LLM call, no sampling — the same chunk
always yields the same statements (the zero-drift determinism rule),
and the template ``prompt`` fields stay reserved for a later LLM
refinement pass. The QA chain rides along: a chunk produced by the
transcript strategy carries ``qa_context.exchange_id``, and every
statement from it links to the same exchange.
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import UTC, datetime
from typing import Any

from ..utils.models import Chunk, Entity
from .templates import TEMPLATE_VERSION, TemplateCatalogue

_SENTENCE_RE = re.compile(r"[^.!?\n]+[.!?]?", re.MULTILINE)
MAX_STATEMENTS_PER_CHUNK = 12


class StatementAtom:
    """One atomized statement, ready for the store."""

    __slots__ = (
        "chunk_id",
        "confidence",
        "document_id",
        "end_char",
        "entities",
        "exchange_id",
        "start_char",
        "statement_hash",
        "template_id",
        "template_version",
        "text",
    )

    def __init__(
        self,
        *,
        chunk_id: str,
        document_id: str,
        text: str,
        template_id: str,
        start_char: int,
        end_char: int,
        entities: list[Entity],
        exchange_id: int | None,
    ) -> None:
        self.chunk_id = chunk_id
        self.document_id = document_id
        self.text = text
        self.template_id = template_id
        self.template_version = TEMPLATE_VERSION
        self.start_char = start_char
        self.end_char = end_char
        self.entities = entities
        self.exchange_id = exchange_id
        # Collision-free preimage: JSON serialization, so entity names or
        # sentence text containing the separator can never alias another
        # (template, entity-set, text) triple.
        preimage = json.dumps(
            {
                "template": template_id,
                "entities": sorted(e.name.lower() for e in entities),
                "text": " ".join(text.lower().split()),
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        self.statement_hash = hashlib.sha256(preimage.encode("utf-8")).hexdigest()
        # Confidence: the weakest anchored entity, damped by entity count
        # uncertainty. Deterministic; no model score of its own.
        self.confidence = min((e.confidence or 0.5 for e in entities), default=0.5)


def _exchange_number(raw: object) -> int | None:
    """The QA exchange number, from an int or the producer's string form.

    The transcript strategy writes ``exchange-0007`` (optionally
    ``-partN`` for budget-split chunks); both carry the same exchange
    sequence number, and that number is what statements store.
    """
    if isinstance(raw, bool):
        return None
    if isinstance(raw, int):
        return raw
    if isinstance(raw, str):
        match = re.search(r"exchange-(\d+)", raw)
        if match:
            return int(match.group(1))
    return None


def atomize_chunk(
    chunk: Chunk,
    entities: list[Entity],
    catalogue: TemplateCatalogue,
    *,
    max_per_chunk: int = MAX_STATEMENTS_PER_CHUNK,
) -> list[StatementAtom]:
    """Split one chunk into classified statement atoms.

    ``max_per_chunk`` is the per-chunk atom cap; it defaults to the
    shipped constant (``statements.max_per_chunk`` mirrors it in config).
    """
    text = chunk.text or ""
    chunk_entities = [e for e in entities if e.chunk_id == chunk.chunk_id]
    exchange_id = None
    qa_context = chunk.metadata.get("qa_context")
    if isinstance(qa_context, dict):
        exchange_id = _exchange_number(qa_context.get("exchange_id"))
    atoms: list[StatementAtom] = []
    for match in _SENTENCE_RE.finditer(text):
        if len(atoms) >= max_per_chunk:
            break
        sentence = match.group(0).strip()
        if len(sentence) < 12:
            continue
        template_id = catalogue.classify(sentence)
        anchored = [
            e
            for e in chunk_entities
            if e.source_start_char is not None
            and match.start() <= e.source_start_char < match.end()
        ]
        if not catalogue.validate_candidate(template_id, len(anchored)):
            continue
        atoms.append(
            StatementAtom(
                chunk_id=chunk.chunk_id or "",
                document_id=chunk.document_id,
                text=sentence,
                template_id=template_id,
                start_char=match.start(),
                end_char=match.end(),
                entities=anchored,
                exchange_id=exchange_id,
            )
        )
    return atoms


def template_histogram(atoms: list[StatementAtom]) -> dict[str, int]:
    """Deterministic template counts for the ingest envelope."""
    histogram: dict[str, int] = {}
    for atom in atoms:
        histogram[atom.template_id] = histogram.get(atom.template_id, 0) + 1
    return dict(sorted(histogram.items()))


def statement_row(
    atom: StatementAtom,
    tenant_id: str,
    *,
    model_version: str | None = None,
    prompt_version: str | None = None,
) -> tuple[Any, ...]:
    """The 15-value insert tuple (pure data; the store owns the SQL)."""
    anchored = [
        {
            "entity_id": e.entity_id,
            "name": e.name,
            "entity_type": e.entity_type,
            "start": e.source_start_char,
            "end": e.source_end_char,
        }
        for e in atom.entities
    ]
    return (
        tenant_id,
        atom.chunk_id,
        atom.document_id,
        atom.statement_hash,
        atom.text,
        atom.template_id,
        atom.template_version,
        atom.start_char,
        atom.end_char,
        json.dumps(anchored),
        atom.exchange_id,
        atom.exchange_id and datetime.now(UTC),
        datetime.now(UTC),
        atom.confidence,
        json.dumps({"model_version": model_version, "prompt_version": prompt_version}),
    )
