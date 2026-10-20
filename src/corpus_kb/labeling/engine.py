"""The LF scoring engine: tenant chunks -> the (n_items, n_LFs) matrix.

Reads the tenant's chunks (id, text, metadata) in keyset batches — the
``topics.engine.read_stored_embeddings`` query style (tenant GUC inside
one transaction, stable ``ORDER BY chunk_id``). Scoring is pure text and
metadata work: no embedder, no network, no RNG. The matrix holds class
indices with ``MISSING`` (-1) abstains, ready for ``aggregate_signals``.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass

import asyncpg
import numpy as np

from corpus_kb.coding.aggregation import MISSING
from corpus_kb.storage.tenant_conn import tenant_connection

from .lfs import LFSpec
from .protocol import LabelingFunction

__all__ = [
    "BATCH_SIZE",
    "MAX_CHUNKS_PER_BUILD",
    "ScoredCorpus",
    "StoredChunk",
    "live_stats",
    "score_lfs",
]

# Working-set bounds: batches keep one query's rows small; the cap keeps a
# runaway corpus from exhausting memory. The cap is a guard, not a target.
MAX_CHUNKS_PER_BUILD = 100_000
BATCH_SIZE = 5_000

# Smallest UUID; every real chunk_id sorts after it (keyset start).
_UUID_FLOOR = "00000000-0000-0000-0000-000000000000"


@dataclass(frozen=True)
class StoredChunk:
    """One chunk text plus its stored metadata (read-only view)."""

    chunk_id: str
    text: str
    metadata: dict[str, object]


@dataclass(frozen=True)
class ScoredCorpus:
    """The scored corpus: chunk order, class vocabulary, LF matrix."""

    classes: tuple[str, ...]
    lf_names: tuple[str, ...]
    chunk_ids: list[str]
    matrix: np.ndarray  # (n_items, n_lfs) int; MISSING = abstain

    @property
    def chunk_count(self) -> int:
        return len(self.chunk_ids)


async def read_chunks(
    pg_pool: asyncpg.Pool, tenant_id: str, limit: int = MAX_CHUNKS_PER_BUILD
) -> list[StoredChunk]:
    """Read the tenant's chunks in keyset batches (tenant GUC, stable order).

    Ordering is by chunk id, so every caller sees one stable sequence.
    ``chunks.metadata`` jsonb carries the web keys metadata LFs read.
    """
    chunks: list[StoredChunk] = []
    async with tenant_connection(pg_pool, tenant_id) as conn:
        last = _UUID_FLOOR
        while len(chunks) < limit:
            rows = await conn.fetch(
                """
                SELECT chunk_id::text AS chunk_id, text, metadata
                FROM chunks
                WHERE tenant_id = $1::uuid AND chunk_id > $2::uuid
                ORDER BY chunk_id
                LIMIT $3
                """,
                tenant_id,
                last,
                min(BATCH_SIZE, limit - len(chunks)),
            )
            if not rows:
                break
            for row in rows:
                chunks.append(
                    StoredChunk(
                        chunk_id=str(row["chunk_id"]),
                        text=str(row["text"]),
                        metadata=_parse_metadata(row["metadata"]),
                    )
                )
            last = str(rows[-1]["chunk_id"])
    return chunks


def _parse_metadata(raw: object) -> dict[str, object]:
    """asyncpg hands jsonb back as a string; parse it, empty on failure."""
    if isinstance(raw, str):
        try:
            parsed: object = json.loads(raw)
        except ValueError:
            return {}
        if isinstance(parsed, dict):
            return parsed
    if isinstance(raw, dict):
        return raw
    return {}


def score_matrix(spec: LFSpec, chunks: Sequence[StoredChunk]) -> np.ndarray:
    """Score every LF over every chunk (pure; deterministic order)."""
    matrix = np.full((len(chunks), len(spec.lfs)), MISSING, dtype=int)
    for column, lf in enumerate(spec.lfs):
        for row, chunk in enumerate(chunks):
            matrix[row, column] = lf.apply(chunk.text, chunk.metadata)
    return matrix


async def score_lfs(
    pg_pool: asyncpg.Pool,
    tenant_id: str,
    spec: LFSpec,
    limit: int = MAX_CHUNKS_PER_BUILD,
) -> ScoredCorpus:
    """Read the tenant's chunks and build the (n_items, n_LFs) matrix."""
    chunks = await read_chunks(pg_pool, tenant_id, limit)
    matrix = score_matrix(spec, chunks)
    return ScoredCorpus(
        classes=spec.classes,
        lf_names=tuple(lf.name for lf in spec.lfs),
        chunk_ids=[chunk.chunk_id for chunk in chunks],
        matrix=matrix,
    )


def spec_from_rows(lf_rows: list[dict[str, object]]) -> LFSpec:
    """Rebuild an LFSpec from registry rows (classes sorted for indices).

    The LF analysis is name-based (coverage, overlap, conflicts,
    polarity), so a sorted class vocabulary resolves the indices without
    the source file. Rows without a resolvable label are skipped.
    """
    labels = sorted({str(row["label"]) for row in lf_rows if isinstance(row.get("label"), str)})
    functions: list[LabelingFunction] = []
    for row in lf_rows:
        label = row.get("label")
        config = row.get("config")
        if not isinstance(label, str) or label not in labels:
            continue
        functions.append(
            LabelingFunction(
                name=str(row["name"]),
                kind=str(row["kind"]),
                label=label,
                label_index=labels.index(label),
                config=config if isinstance(config, dict) else {},
            )
        )
    return LFSpec(classes=tuple(labels), lfs=tuple(functions))


async def live_stats(
    pg_pool: asyncpg.Pool, tenant_id: str, lf_rows: list[dict[str, object]], window: int
) -> dict[str, dict[str, object]]:
    """Per-LF stats over a bounded chunk window (the registry-read helper).

    The read must stay cheap, so stats are computed over the first
    ``window`` chunks by chunk id and the window size rides every row.
    Zero chunks (or no usable LFs) answer zeroed stats, never guesses.
    """
    from . import analysis as lf_analysis  # local: keeps engine's imports flat

    spec = spec_from_rows(lf_rows)
    names = [str(row["name"]) for row in lf_rows]
    if not spec.lfs:
        return {name: _zero_stats() for name in names}
    chunks = await read_chunks(pg_pool, tenant_id, window)
    if not chunks:
        return {name: _zero_stats() for name in names}
    matrix = score_matrix(spec, chunks)
    scored = {
        stat.name: {**stat.to_dict(), "sample_chunks": len(chunks)}
        for stat in lf_analysis.lf_stats(matrix, spec)
    }
    return {name: scored.get(name) or _zero_stats() for name in names}


def _zero_stats() -> dict[str, object]:
    """The honest empty scorecard (nothing was scored)."""
    return {
        "coverage": 0.0,
        "overlap": 0.0,
        "conflicts": 0.0,
        "fired": 0,
        "polarity": {},
        "sample_chunks": 0,
    }
