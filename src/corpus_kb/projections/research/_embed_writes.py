"""Vector-write legs of the research embed path (split from _embed.py).

Both writers maintain the 1024-d vector AND its 256-d MRL slice. Postgres
derives the slice in SQL (``l2_normalize(subvector(...))``); DuckDB has no
subvector/l2_normalize pair, so the slice is normalized in Python and bound
as ``FLOAT[256]`` — same math, declared divergence (storage portability
program §13, per-module twins).
"""

from __future__ import annotations

import math
from typing import Any

from corpus_kb.projections.research._common import opt_str
from corpus_kb.projections.research._embed import ResearchEmbedder
from corpus_kb.research.chunking import build_context_prefix, speaker_label
from corpus_kb.research.conn import dialect_of, tenant_scope

__all__ = ["apply_unit_embeddings", "embed_exchange_texts", "turn_prefixes"]


def turn_prefixes(
    doc: Any, turns: list[dict[str, Any]], pseudonyms: dict[str, str | None]
) -> dict[str, str]:
    """Deterministic per-turn embed prefixes (todo-12, v5 5.2)."""
    if doc is None:
        return {}
    project = opt_str(doc["project_name"])
    title = opt_str(doc["title"])
    source_type = opt_str(doc["source_type"])
    prefixes: dict[str, str] = {}
    for turn in turns:
        label = str(turn.get("speaker", "unknown"))
        prefixes[str(turn["text_sha256"])] = build_context_prefix(
            project=project,
            doc_title=title,
            source_type=source_type,
            speaker=speaker_label(label, opt_str(turn.get("role")), pseudonyms.get(label)),
            timestamp=_turn_timestamp(turn),
        )
    return prefixes


def _turn_timestamp(turn: dict[str, Any]) -> str | None:
    t_start = turn.get("t_start")
    return f"{float(t_start):g}s" if t_start is not None else None


async def apply_unit_embeddings(
    store: Any,
    embedder: ResearchEmbedder,
    tenant_id: Any,
    doc_id: Any,
    texts_by_sha: dict[str, str],
    prefixes_by_sha: dict[str, str] | None = None,
) -> None:
    """Embed unit texts through the cache and maintain embedding_256.

    The embedded string is the DETERMINISTIC context prefix + clean text
    (todo-12, v5 5.2 contextual retrieval); the stored text column stays
    clean for generation and citation. The prefix is a pure function of
    row context, so rebuilds hit the same cache entries and make zero
    embedder calls beyond misses.
    """
    duck = dialect_of(store).is_duckdb
    prefixes = prefixes_by_sha or {}
    for text_sha, text in texts_by_sha.items():
        embed_text = f"{prefixes[text_sha]}\n{text}" if prefixes.get(text_sha) else text
        vector = await embedder.embed_cached(tenant_id, embed_text)
        if vector is None:
            continue
        literal = _vector_literal(vector)
        if duck:
            sql = """
                UPDATE research_units SET
                    embedding = $3::FLOAT[1024],
                    embedding_256 = $8::FLOAT[256],
                    embedding_model = $4, model_revision = $5, dimensions = $6
                WHERE tenant_id = $1 AND doc_id = $2 AND text_sha256 = $7
                """
            extra: tuple[Any, ...] = (_mrl_256_literal(vector),)
        else:
            sql = """
                UPDATE research_units SET
                    embedding = $3::vector,
                    embedding_256 = l2_normalize(subvector($3::vector, 1, 256)),
                    embedding_model = $4, model_revision = $5, dimensions = $6
                WHERE tenant_id = $1 AND doc_id = $2 AND text_sha256 = $7
                """
            extra = ()
        async with tenant_scope(store, tenant_id) as conn:
            await conn.execute(
                sql,
                str(tenant_id),
                str(doc_id),
                literal,
                embedder.model,
                embedder.model_revision,
                1024,
                text_sha,
                *extra,
            )


async def embed_exchange_texts(
    store: Any,
    embedder: ResearchEmbedder,
    tenant_id: Any,
    doc_id: Any,
    texts_by_exchange_id: dict[int, str],
) -> int:
    """Embed exchange qa texts (prefix + qa_text) into research_exchanges.

    The exchange is the interview RETRIEVAL parent and the qa CHILD source:
    its embedding indexes the combined "Q: ... A: ..." text so queries
    matching either side of the exchange retrieve it. Cache-backed like the
    unit path; returns the number of exchange vectors written.
    """
    duck = dialect_of(store).is_duckdb
    written = 0
    for exchange_id, embed_text in texts_by_exchange_id.items():
        vector = await embedder.embed_cached(tenant_id, embed_text)
        if vector is None:
            continue
        literal = _vector_literal(vector)
        if duck:
            sql = """
                UPDATE research_exchanges SET
                    embedding = $3::FLOAT[1024],
                    embedding_256 = $8::FLOAT[256],
                    embedding_model = $4, model_revision = $5, dimensions = $6
                WHERE tenant_id = $1 AND doc_id = $2 AND exchange_id = $7
                """
            extra: tuple[Any, ...] = (_mrl_256_literal(vector),)
        else:
            sql = """
                UPDATE research_exchanges SET
                    embedding = $3::vector,
                    embedding_256 = l2_normalize(subvector($3::vector, 1, 256)),
                    embedding_model = $4, model_revision = $5, dimensions = $6
                WHERE tenant_id = $1 AND doc_id = $2 AND exchange_id = $7
                """
            extra = ()
        async with tenant_scope(store, tenant_id) as conn:
            result = await conn.execute(
                sql,
                str(tenant_id),
                str(doc_id),
                literal,
                embedder.model,
                embedder.model_revision,
                1024,
                int(exchange_id),
                *extra,
            )
        written += 0 if str(result).endswith("0") else 1
    return written


def _vector_literal(vector: list[float]) -> str:
    """Compact no-space pgvector/varchar literal (the house format)."""
    return "[" + ",".join(f"{v:.9g}" for v in vector) + "]"


def _mrl_256_literal(vector: list[float]) -> str:
    """The 256-d MRL slice, L2-renormalized, as a compact vector literal."""
    sliced = vector[:256]
    norm = math.sqrt(sum(float(x) * float(x) for x in sliced))
    normalized = [float(x) / norm for x in sliced] if norm else [0.0] * len(sliced)
    return _vector_literal(normalized)
