"""Deterministic turn-aligned chunk planning for the transcript strategy.

Pure functions: no strategy state, no model calls. Groups speaker turns into
Q+A exchange chunks (each carrying ``qa_context``) and meeting-window chunks
for facilitator-less leftovers (no invented questions), all split at the
0.8x2048-token budget on turn boundaries.
"""

from __future__ import annotations

from collections.abc import Mapping

from ...domain.transcript import TurnSpec
from ...research.chunking import meeting_windows
from ...research.linking import ExchangeSpec
from ..base import QA_CONTEXT_KEY, TOKEN_BUDGET_CHARS, TRANSCRIPT_STRATEGY_NAME, PlannedChunk
from ..tokenizer import hard_split_text


def positioned_chunks(
    turns: list[TurnSpec],
    exchanges: list[ExchangeSpec],
    starts: list[int],
    warnings: list[str],
    config: Mapping[str, object] | None = None,
) -> list[tuple[int, list[PlannedChunk]]]:
    """Chunk groups keyed by their first turn's position (document order)."""
    groups, order = _exchange_groups(turns, exchanges)
    out = _qa_chunk_groups(groups, order, exchanges, starts, warnings, config)
    out.extend(_window_chunk_groups(turns, groups, starts, warnings, config))
    return out


def _exchange_groups(
    turns: list[TurnSpec], exchanges: list[ExchangeSpec]
) -> tuple[dict[int, list[TurnSpec]], list[int]]:
    """Group turns by their (first-claiming) exchange, in first-appearance order.

    cross_ref linking can share question seqs between exchanges; first claim
    wins so chunks never overlap.
    """
    owner: dict[int, int] = {}
    for exchange in exchanges:
        for seq in (*exchange.q_unit_seqs, *exchange.a_unit_seqs):
            owner.setdefault(seq, exchange.seq)
    by_seq = {turn.seq: turn for turn in turns}
    groups: dict[int, list[TurnSpec]] = {}
    order: list[int] = []
    for turn in turns:
        exchange_seq = owner.get(turn.seq)
        if exchange_seq is None:
            continue
        if exchange_seq not in groups:
            groups[exchange_seq] = []
            order.append(exchange_seq)
        groups[exchange_seq].append(by_seq[turn.seq])
    return groups, order


def _qa_chunk_groups(
    groups: dict[int, list[TurnSpec]],
    order: list[int],
    exchanges: list[ExchangeSpec],
    starts: list[int],
    warnings: list[str],
    config: Mapping[str, object] | None = None,
) -> list[tuple[int, list[PlannedChunk]]]:
    """One budget-bounded chunk group per exchange, each part carrying qa_context."""
    by_seq = {exchange.seq: exchange for exchange in exchanges}
    out: list[tuple[int, list[PlannedChunk]]] = []
    for exchange_seq in order:
        subset = groups[exchange_seq]
        exchange = by_seq[exchange_seq]
        question = " ".join(turn.text for turn in subset if turn.seq in set(exchange.q_unit_seqs))
        parts = split_to_budget(subset, warnings, config)
        chunks: list[PlannedChunk] = []
        for k, part in enumerate(parts):
            exchange_id = f"exchange-{exchange_seq:04d}"
            if k:
                exchange_id = f"{exchange_id}-part{k + 1}"
            metadata = _qa_metadata(exchange, part, question, exchange_id)
            chunks.append(_planned(part, starts, metadata))
        out.append((subset[0].seq, chunks))
    return out


def _qa_metadata(
    exchange: ExchangeSpec, part: list[TurnSpec], question: str, exchange_id: str
) -> dict[str, object]:
    return {
        "strategy": TRANSCRIPT_STRATEGY_NAME,
        "grouping": "qa",
        "turn_seqs": [turn.seq for turn in part],
        QA_CONTEXT_KEY: {
            "question": question,
            "participants": sorted({turn.speaker for turn in part}),
            "exchange_id": exchange_id,
        },
    }


def _window_chunk_groups(
    turns: list[TurnSpec],
    groups: dict[int, list[TurnSpec]],
    starts: list[int],
    warnings: list[str],
    config: Mapping[str, object] | None = None,
) -> list[tuple[int, list[PlannedChunk]]]:
    """Facilitator-less leftovers -> meeting windows WITHOUT qa_context."""
    claimed = {turn.seq for subset in groups.values() for turn in subset}
    leftover = [turn.seq for turn in turns if turn.seq not in claimed]
    out: list[tuple[int, list[PlannedChunk]]] = []
    for window in meeting_windows(leftover):
        subset = [turn for turn in turns if turn.seq in set(window.seqs)]
        metadata: dict[str, object] = {
            "strategy": TRANSCRIPT_STRATEGY_NAME,
            "grouping": "turns",
            "turn_seqs": list(window.seqs),
        }
        parts = split_to_budget(subset, warnings, config)
        out.append((window.start_seq, [_planned(part, starts, dict(metadata)) for part in parts]))
    return out


def reconstruct_turns(turns: list[TurnSpec]) -> tuple[str, list[int]]:
    """Canonical 'Speaker: text' reconstruction + per-turn start line (0-based)."""
    lines: list[str] = []
    starts: list[int] = []
    for turn in turns:
        starts.append(len(lines))
        lines.append(f"{turn.speaker}: {turn.text}")
    return "\n".join(lines), starts


def _planned(part: list[TurnSpec], starts: list[int], metadata: dict[str, object]) -> PlannedChunk:
    first, last = part[0], part[-1]
    return PlannedChunk(
        text="\n".join(f"{turn.speaker}: {turn.text}" for turn in part),
        index=0,  # re-indexed by the strategy in document order
        metadata=metadata,
        source_type="text",
        start_line=starts[first.seq] + 1,
        end_line=starts[last.seq] + 1 + last.text.count("\n"),
    )


def split_to_budget(
    subset: list[TurnSpec],
    warnings: list[str],
    config: Mapping[str, object] | None = None,
) -> list[list[TurnSpec]]:
    """Greedy turn-boundary split at the token budget; oversized turns hard-split."""
    parts: list[list[TurnSpec]] = []
    current: list[TurnSpec] = []
    current_len = 0
    for turn in subset:
        turn_len = len(turn.speaker) + len(turn.text) + 2
        if turn_len > TOKEN_BUDGET_CHARS:
            if current:
                parts.append(current)
                current, current_len = [], 0
            warnings.append(f"turn seq={turn.seq} exceeds token budget; hard-split")
            parts.extend([piece] for piece in _hard_split_turn(turn, config, warnings))
            continue
        if current and current_len + turn_len > TOKEN_BUDGET_CHARS:
            parts.append(current)
            current, current_len = [], 0
        current.append(turn)
        current_len += turn_len
    if current:
        parts.append(current)
    return parts or [[]]


def _hard_split_turn(
    turn: TurnSpec,
    config: Mapping[str, object] | None = None,
    warnings: list[str] | None = None,
) -> list[TurnSpec]:
    # Budget accounts for the render overhead ("Speaker: " + newline) so each
    # piece's rendered line stays within the token budget. With
    # ``ingestion.tokenizer: semchunk`` the split lands on sentence or word
    # boundaries with native offsets (tokenizer.hard_split_text); the
    # default stays the historical fixed char slices, byte for byte.
    budget = max(TOKEN_BUDGET_CHARS - len(turn.speaker) - 2, 1)
    pieces = hard_split_text(turn.text, budget_chars=budget, config=config, warnings=warnings)
    return [
        TurnSpec(seq=turn.seq, speaker=turn.speaker, text=piece, role=turn.role) for piece in pieces
    ]
