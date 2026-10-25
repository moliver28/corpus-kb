"""Tune-data builders for ``extraction_tune`` (Living Corpus W5).

Two artifact shapes from one mining pass over confirmed corpus
evidence: LangExtract seed exemplars (profile seeds for ``customized``
mode) and GLiNER training examples (token-level ``ner`` spans for the
LoRA arm). Confirmed evidence is trusted in this order: human-resolved
conflict statements first (the review decision is the strongest
signal), then high-confidence stored relations.
"""

from __future__ import annotations

import json
from typing import Any

import asyncpg

from .icl import _locate

# Statements that survived a human resolve: revision-resolved pairs keep
# statement_b (the accepted correction); dismissed pairs keep both.
# min_confidence filters on the statement's own extraction confidence.
_CONFIRMED_SQL = """
SELECT s.statement_id::text AS statement_id,
       s.text AS statement_text,
       s.entities AS entities
FROM statement_conflicts sc
JOIN statements s ON s.statement_id = sc.statement_b_id
   AND s.tenant_id = sc.tenant_id
WHERE sc.tenant_id = $1::uuid AND sc.resolved_verdict = 'revision'
  AND s.confidence >= $2::float
ORDER BY s.statement_id
LIMIT $3
"""


async def mine_confirmed_statements(
    pool: asyncpg.Pool,
    tenant_id: str,
    limit: int = 24,
    min_confidence: float = 0.0,
) -> list[dict[str, Any]]:
    """Statement texts plus their anchored entities from resolved reviews."""
    async with pool.acquire() as conn, conn.transaction():
        await conn.execute("SELECT set_config('app.current_tenant_id', $1, true)", tenant_id)
        rows = await conn.fetch(_CONFIRMED_SQL, tenant_id, float(min_confidence), limit)
    out: list[dict[str, Any]] = []
    for row in rows:
        raw = row["entities"]
        if isinstance(raw, str):
            raw = json.loads(raw)
        out.append(
            {
                "statement_id": row["statement_id"],
                "text": row["statement_text"] or "",
                "entities": raw if isinstance(raw, list) else [],
            }
        )
    return out


def statements_to_exemplars(
    confirmed: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Resolved statements as LangExtract exemplars (locatable names only)."""
    exemplars: list[dict[str, Any]] = []
    for item in confirmed:
        text = str(item.get("text") or "")
        extractions: list[dict[str, Any]] = []
        for entity in item.get("entities") or []:
            if not isinstance(entity, dict):
                continue
            name = entity.get("name")
            span = _locate(text, name)
            if span is None:
                continue
            extractions.append(
                {
                    "extraction_class": entity.get("entity_type"),
                    "extraction_text": name,
                    "start_pos": span[0],
                    "end_pos": span[1],
                }
            )
        if extractions:
            exemplars.append({"text": text, "extractions": extractions})
    return exemplars


def tokenize_with_offsets(text: str) -> tuple[list[str], list[tuple[int, int]]]:
    """Whitespace tokens plus their character spans (for GLiNER data)."""
    tokens: list[str] = []
    spans: list[tuple[int, int]] = []
    start = 0
    for piece in text.split():
        index = text.find(piece, start)
        tokens.append(piece)
        spans.append((index, index + len(piece)))
        start = index + len(piece)
    return tokens, spans


def to_gliner_example(text: str, entities: list[tuple[str, str]]) -> dict[str, Any] | None:
    """One GLiNER training row ({tokenized_text, ner}), or None.

    ``entities`` are (name, label) pairs located by literal search; pairs
    whose mention cannot be located are dropped (a wrong token span is
    worse than a missing example). Token indices are inclusive, the
    format gliner's trainer reads.
    """
    tokens, spans = tokenize_with_offsets(text)
    ner: list[list[Any]] = []
    for name, label in entities:
        char_span = _locate(text, name)
        if char_span is None:
            continue
        token_indices = [
            i for i, (s, e) in enumerate(spans) if s >= char_span[0] and e <= char_span[1]
        ]
        if not token_indices:
            continue
        ner.append([token_indices[0], token_indices[-1], label])
    if not ner:
        return None
    return {"tokenized_text": tokens, "ner": ner}
