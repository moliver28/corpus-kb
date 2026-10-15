"""Config-v2 retrieval wiring tests: mode enum + live arm candidate LIMITs.

Two contracts land in W1:

* ``retrieval.mode`` is enum-locked to ``hybrid_rrf`` — any other value is
  refused at load time with a remediation message (the key exists so
  future modes are additive).
* ``retrieval.vector_candidates`` / ``lexical_candidates`` drive the LIVE
  arm LIMITs (``max(candidates, k * 2)``). The old benchmark-only
  ``candidates_per_branch`` knob is gone; the alias layer feeds the split
  keys when only the legacy name is present.

Offline: recording connection doubles, no database.
"""

from __future__ import annotations

from typing import cast
from unittest.mock import AsyncMock, MagicMock
from uuid import UUID, uuid4

import pytest

from corpus_kb.domain.models import (
    DEFAULT_TENANT_ID,
    SearchQuery,
    SearchSimilarQuery,
)
from corpus_kb.handlers.query_handler import QueryHandler
from corpus_kb.handlers.query_vector_arms import ollama_vector_arm, pgml_vector_arm
from corpus_kb.research.retrieval_settings import (
    RetrievalSettings,
    load_retrieval_settings,
    mode_error,
)
from tests.mock_pg import transaction_cm

# ============================================================================
# mode enum
# ============================================================================


def test_mode_defaults_to_hybrid_rrf() -> None:
    settings = load_retrieval_settings({})
    assert settings.mode == "hybrid_rrf"


def test_mode_rejects_other_values() -> None:
    with pytest.raises(ValueError, match="hybrid_rrf"):
        load_retrieval_settings({"retrieval": {"mode": "vector_only"}})
    with pytest.raises(ValueError, match="hybrid_rrf"):
        load_retrieval_settings({"retrieval": {"mode": "HYBRID_RRF"}})
    assert mode_error("vector_only") is not None
    assert mode_error("hybrid_rrf") is None


def test_mode_is_validated_at_handler_construction() -> None:
    pool = MagicMock()
    with pytest.raises(ValueError, match="hybrid_rrf"):
        QueryHandler(cast("object", pool), config={"retrieval": {"mode": "bm25"}})


# ============================================================================
# typed settings: the split candidate keys
# ============================================================================


def test_candidate_settings_defaults_and_reads() -> None:
    settings = load_retrieval_settings({})
    assert settings.vector_candidates == 150
    assert settings.lexical_candidates == 150
    assert settings.rrf_k == 60
    assert settings.expand_to_parent is True
    assert settings.exclude_degraded is True
    loaded = load_retrieval_settings(
        {
            "retrieval": {
                "vector_candidates": 20,
                "lexical_candidates": 40,
                "expand_to_parent": False,
            }
        }
    )
    assert loaded.vector_candidates == 20
    assert loaded.lexical_candidates == 40
    assert loaded.expand_to_parent is False


def test_candidate_settings_reject_bad_values() -> None:
    with pytest.raises(ValueError, match="vector_candidates"):
        load_retrieval_settings({"retrieval": {"vector_candidates": 0}})
    with pytest.raises(ValueError, match="lexical_candidates"):
        load_retrieval_settings({"retrieval": {"lexical_candidates": "150"}})


# ============================================================================
# live arm LIMIT wiring (recording conn asserts the parameter values)
# ============================================================================


class _RecordingConn:
    """Captures SQL + params; returns no rows."""

    def __init__(self) -> None:
        self.sql: list[str] = []
        self.params: list[tuple[object, ...]] = []

    async def execute(self, sql: str, *args: object) -> str:
        self.sql.append(sql)
        self.params.append(args)
        return "OK"

    async def fetch(self, sql: str, *args: object) -> list[object]:
        self.sql.append(sql)
        self.params.append(args)
        return []

    async def fetchrow(self, sql: str, *args: object) -> dict[str, object] | None:
        self.sql.append(sql)
        self.params.append(args)
        return None

    def transaction(self):
        outer = self

        class _Tx:
            async def __aenter__(self) -> _RecordingConn:
                return outer

            async def __aexit__(self, *exc: object) -> bool:
                return False

        return _Tx()


def _embedder(dimensions: int = 2560) -> MagicMock:
    embedder = MagicMock()
    embedder.embed.return_value = [0.1] * dimensions
    embedder.embed_matryoshka.return_value = [0.1] * dimensions
    embedder.dimensions = dimensions
    embedder.model = "qwen3-embedding:4b"
    return embedder


def _limit_params(conn: _RecordingConn) -> list[object]:
    """The LIMIT-parameter values from the vector-arm fetch calls ($3/$4)."""
    limits: list[object] = []
    for sql, args in zip(conn.sql, conn.params, strict=True):
        if "<=>" not in sql:
            continue
        if "LIMIT $3" in sql:
            limits.append(args[2])
        elif "LIMIT $4" in sql:
            limits.append(args[3])
    return limits


@pytest.mark.asyncio
async def test_vector_arm_limit_uses_vector_candidates_floor_k_times_two() -> None:
    conn = _RecordingConn()
    rows, reason = await ollama_vector_arm(
        cast("object", conn),
        embedder=_embedder(),
        semantic_query="housing policy",
        tenant_id=DEFAULT_TENANT_ID,
        k=5,
        vector_candidates=150,
        dynamic_predicates=[],
        degraded_pred="",
        matryoshka_enabled=False,
        matryoshka_dim=1024,
        candidate_multiplier=8,
        exact_threshold=0.05,
    )
    assert rows == [] and reason is None
    limits = _limit_params(conn)
    assert limits, "vector arm did not issue a LIMITed fetch"
    assert all(limit == 150 for limit in limits)


@pytest.mark.asyncio
async def test_vector_arm_limit_floors_at_k_times_two() -> None:
    """A tiny candidate config never under-fetches below k * 2."""
    conn = _RecordingConn()
    rows, reason = await ollama_vector_arm(
        cast("object", conn),
        embedder=_embedder(),
        semantic_query="housing policy",
        tenant_id=DEFAULT_TENANT_ID,
        k=25,
        vector_candidates=10,
        dynamic_predicates=[],
        degraded_pred="",
        matryoshka_enabled=False,
        matryoshka_dim=1024,
        candidate_multiplier=8,
        exact_threshold=0.05,
    )
    assert rows == [] and reason is None
    limits = _limit_params(conn)
    assert limits, "vector arm did not issue a LIMITed fetch"
    assert all(limit == 50 for limit in limits)  # max(10, 25 * 2)


@pytest.mark.asyncio
async def test_pgml_arm_limit_uses_vector_candidates() -> None:
    conn = _RecordingConn()
    rows, reason = await pgml_vector_arm(
        cast("object", conn),
        model="qwen3-embedding:4b",
        semantic_query="housing policy",
        tenant_id=DEFAULT_TENANT_ID,
        k=5,
        vector_candidates=42,
        dynamic_predicates=[],
        degraded_pred="",
        exact_threshold=0.05,
    )
    assert rows == [] and reason is None
    limits = _limit_params(conn)
    assert limits, "pgml vector arm did not issue a LIMITed fetch"
    assert all(limit == 42 for limit in limits)


@pytest.mark.asyncio
async def test_fts_arm_limit_uses_lexical_candidates() -> None:
    """The lexical arm's LIMIT comes from retrieval.lexical_candidates."""
    cid = UUID("00000000-0000-0000-0000-000000000001")
    did = UUID("0000-0000-0000-0000-0000-00000000000f")
    mock_conn = AsyncMock()
    mock_conn.execute = AsyncMock()
    mock_conn.transaction = MagicMock(return_value=transaction_cm())
    mock_conn.fetch = AsyncMock(
        side_effect=[
            [],  # vector arm: no embedder wired -> skipped, see config below
            [{"chunk_id": cid, "text": "fts hit", "doc_id": did, "source": "s", "score": 0.5}],
            [],  # fusion
        ]
    )
    mock_pool = MagicMock()
    mock_pool.acquire.return_value.__aenter__ = AsyncMock(return_value=mock_conn)
    mock_pool.acquire.return_value.__aexit__ = AsyncMock(return_value=False)

    handler = QueryHandler(
        cast("object", mock_pool),
        config={
            "embedding": {"provider": "ollama"},
            "reranking": {"enabled": False},
            "retrieval": {"lexical_candidates": 123},
        },
    )
    await handler.handle_search(SearchQuery(query="test", k=1))

    fts_call = mock_conn.fetch.call_args_list[0]
    # args = (sql, semantic_query, tenant_id, limit, *filter_params)
    assert fts_call.args[3] == 123


@pytest.mark.asyncio
async def test_candidates_alias_feeds_live_limits_end_to_end() -> None:
    """Legacy search.candidates_per_branch arms BOTH live LIMITs."""
    from corpus_kb.config_aliases import apply_legacy_aliases

    config: dict[str, object] = {
        "embedding": {"provider": "ollama"},
        "search": {"candidates_per_branch": 33},
        "reranking": {"enabled": False},
    }
    apply_legacy_aliases(config)
    handler = QueryHandler(
        cast("object", MagicMock()),
        config=config,
    )
    assert handler._retrieval_settings.vector_candidates == 33
    assert handler._retrieval_settings.lexical_candidates == 33


def test_expand_to_parent_flipped_on_by_default() -> None:
    settings = RetrievalSettings()
    assert settings.expand_to_parent is True
    handler_config: dict[str, object] = {}
    handler = QueryHandler(
        cast("object", MagicMock()),
        config=handler_config,
    )
    assert handler._retrieval_settings.expand_to_parent is True


def test_search_similar_signature_unchanged() -> None:
    """SearchSimilarQuery still carries its own k (no envelope change in W1)."""
    query = SearchSimilarQuery(chunk_id=uuid4(), tenant_id=DEFAULT_TENANT_ID, k=7)
    assert query.k == 7
