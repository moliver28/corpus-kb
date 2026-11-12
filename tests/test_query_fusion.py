"""Unit tests for handlers.query_fusion.rrf_fuse — the migration-024 port.

The SQL function corpus.rrf_fusion (migrations/024_rrf_fusion_tiebreak.sql)
stays the source of truth; every test here pins one mirrored behavior:
1-based leg positions as ranks, contribution 1/(rrf_k + rank) with the
SQL default constant 60, per-chunk score summation across legs, the
lexical-side-wins metadata rule, and the total order fscore DESC then
chunk id ASC. All offline — no Postgres, no Ollama.

The N-leg entry point ``rrf_fuse_legs`` (issue #136 multi-query) shares
the same formula: the two-leg function delegates to it, and the tests at
the bottom pin the N-leg sums, metadata rule, and two-leg equivalence.
"""

from __future__ import annotations

from uuid import UUID, uuid5

from corpus_kb.handlers.query_fusion import rrf_fuse, rrf_fuse_legs

# Fixed namespace so tiebreaker keys are stable across runs (same trick as
# tests/test_query_determinism.py).
_NS = UUID("6f6f6f6f-6f6f-6f6f-6f6f-6f6f6f6f6f6f")


def _cid(name: str) -> str:
    return str(uuid5(_NS, name))


def _row(
    chunk_id: str | UUID,
    *,
    text: str = "",
    source: str = "s",
    doc_id: str | None = None,
    score: float = 0.0,
) -> dict[str, object]:
    return {
        "chunk_id": chunk_id,
        "text": text or f"t-{str(chunk_id)[:8]}",
        "source": source,
        "doc_id": doc_id or str(uuid5(_NS, "doc")),
        "score": score,
    }


def test_empty_legs_return_no_rows() -> None:
    assert rrf_fuse([], [], k=10) == []


def test_single_lexical_leg_ranks_by_position() -> None:
    """One leg alone: score is 1/(60 + position), input order = rank order."""
    ids = [_cid(f"c{i}") for i in range(3)]
    fused = rrf_fuse([_row(cid) for cid in ids], [], k=10)
    assert [r.chunk_id for r in fused] == [UUID(cid) for cid in ids]
    assert [r.score for r in fused] == [1.0 / 61, 1.0 / 62, 1.0 / 63]


def test_single_dense_leg_ranks_by_position() -> None:
    ids = [_cid(f"d{i}") for i in range(2)]
    fused = rrf_fuse([], [_row(cid) for cid in ids], k=10)
    assert [r.chunk_id for r in fused] == [UUID(cid) for cid in ids]
    assert [r.score for r in fused] == [1.0 / 61, 1.0 / 62]


def test_overlapping_chunk_id_sums_both_legs() -> None:
    """A chunk hit by both legs scores 1/(60+r_dense) + 1/(60+r_lexical)."""
    hit, other = _cid("hit"), _cid("other")
    dense = [_row(other), _row(hit)]  # hit at dense rank 2
    lexical = [_row(hit, text="lex")]  # hit at lexical rank 1
    fused = rrf_fuse(lexical, dense, k=10)
    by_id = {r.chunk_id: r for r in fused}
    assert by_id[UUID(hit)].score == 1.0 / 61 + 1.0 / 62
    assert by_id[UUID(other)].score == 1.0 / 61
    assert by_id[UUID(hit)].text == "lex"  # lexical side wins metadata


def test_duplicate_row_inside_one_leg_counts_each_position() -> None:
    """SQL UNION ALL: two positions in one leg both contribute, and the
    last position's metadata wins (DISTINCT ON ... ord DESC)."""
    hit = _cid("dup")
    dense = [_row(hit, text="first"), _row(hit, text="second")]
    fused = rrf_fuse([], dense, k=10)
    assert len(fused) == 1
    assert fused[0].score == 1.0 / 61 + 1.0 / 62
    assert fused[0].text == "second"


def test_tied_fscores_order_by_chunk_id_asc() -> None:
    """Same rank in different legs -> equal fscore -> chunk id ASC decides."""
    a, b = _cid("chunk-0"), _cid("chunk-1")
    dense = [_row(a)]  # rank 1 -> 1/61
    lexical = [_row(b)]  # rank 1 -> 1/61 (tie)
    fused = rrf_fuse(lexical, dense, k=10)
    expected = sorted([UUID(a), UUID(b)])
    assert [r.chunk_id for r in fused] == expected


def test_total_order_is_fscore_desc_then_cid_asc() -> None:
    """The migration-024 total order holds over a mixed fixture."""
    ids = [_cid(f"mix{i}") for i in range(4)]
    dense = [_row(ids[3]), _row(ids[2])]  # ranks 1, 2
    lexical = [_row(ids[0]), _row(ids[2])]  # ranks 1, 2 (ids[2] in both)
    fused = rrf_fuse(lexical, dense, k=10)
    order = [(r.chunk_id, r.score) for r in fused]
    # ids[2] fused both legs (2/62); ids[3] and ids[0] tie at 1/61.
    assert order == [
        (UUID(ids[2]), 1.0 / 62 + 1.0 / 62),
        *[(UUID(cid), 1.0 / 61) for cid in sorted([ids[0], ids[3]])],
    ]


def test_leg_argument_order_does_not_change_ids_or_scores() -> None:
    """Swapping the legs keeps every rank (positions stay inside each leg),
    so the fused id/score sequence is identical — the output order is a
    pure function of the legs, not of which argument held which."""
    ids = [_cid(f"swap{i}") for i in range(3)]
    dense = [_row(cid) for cid in ids]
    lexical = [_row(cid, text="lex") for cid in reversed(ids)]
    one = rrf_fuse(lexical, dense, k=10)
    two = rrf_fuse(dense, lexical, k=10)
    assert [(r.chunk_id, r.score) for r in one] == [(r.chunk_id, r.score) for r in two]
    # Repeated runs are bit-identical (snapshot-safe).
    assert [r.score for r in one] == [r.score for r in rrf_fuse(lexical, dense, k=10)]


def test_k_caps_output_after_ordering() -> None:
    ids = [_cid(f"k{i}") for i in range(4)]
    rows = [_row(cid) for cid in ids]
    fused = rrf_fuse([], rows, k=2)
    assert [r.chunk_id for r in fused] == [UUID(cid) for cid in ids[:2]]
    assert len(rrf_fuse([], rows, k=100)) == 4


def test_uuid_and_string_ids_fuse_identically() -> None:
    """The ::uuid cast: a leg carrying UUID objects and the same leg with
    canonical strings produce the same fused rows."""
    ids = [_cid(f"u{i}") for i in range(2)]
    uuid_rows = [_row(UUID(cid)) for cid in ids]
    str_rows = [_row(cid) for cid in ids]
    one = rrf_fuse([], uuid_rows, k=10)
    two = rrf_fuse([], str_rows, k=10)
    assert one == two
    assert all(isinstance(r.chunk_id, UUID) for r in one)


def test_custom_rrf_k_matches_sql_constant_swap() -> None:
    """The handler passes retrieval.rrf_k through; scores follow it."""
    cid = _cid("k60")
    fused = rrf_fuse([_row(cid)], [], k=10, rrf_k=42)
    assert fused[0].score == 1.0 / 43


# ============================================================================
# Issue #136: the N-leg entry point behind multi-query fusion
# ============================================================================


def test_legs_two_leg_call_matches_rrf_fuse() -> None:
    """rrf_fuse(lexical, dense) is exactly rrf_fuse_legs((dense, lexical))."""
    ids = [_cid(f"eq{i}") for i in range(3)]
    dense = [_row(ids[2]), _row(ids[1])]
    lexical = [_row(ids[0], text="lex"), _row(ids[1], text="lex")]
    one = rrf_fuse(lexical, dense, k=10)
    two = rrf_fuse_legs((dense, lexical), k=10)
    assert one == two


def test_legs_sum_every_leg_position() -> None:
    """N legs: a chunk hit at rank 1 in each scores 3/(rrf_k + 1)."""
    hit, other = _cid("n-hit"), _cid("n-other")
    legs = (
        [_row(hit)],  # rewrite 1 dense
        [_row(hit, text="l1")],  # rewrite 1 lexical
        [_row(hit, text="l2")],  # rewrite 2 lexical
        [_row(other)],  # a chunk only rewrite 2 saw
    )
    fused = rrf_fuse_legs(legs, k=10)
    by_id = {r.chunk_id: r for r in fused}
    assert by_id[UUID(hit)].score == 3.0 / 61
    assert by_id[UUID(other)].score == 1.0 / 61
    assert by_id[UUID(hit)].text == "l2"  # the LAST leg wins metadata


def test_legs_rank_stays_inside_each_leg() -> None:
    """Ranks never run across legs: leg 2's rank-1 hit contributes 1/61,
    not 1/(60 + len(leg1) + 1) — the SQL UNION ALL shape."""
    first = [_row(_cid(f"a{i}")) for i in range(150)]  # ranks 1..150
    legs = (first, [_row(_cid("b-top"))])
    fused = rrf_fuse_legs(legs, k=300)
    by_id = {r.chunk_id: r for r in fused}
    assert by_id[UUID(_cid("b-top"))].score == 1.0 / 61


def test_legs_total_order_and_k_cap() -> None:
    """Same total order (fscore DESC, chunk id ASC) and LIMIT over N legs."""
    ids = [_cid(f"n{i}") for i in range(3)]
    legs = (
        [_row(ids[2]), _row(ids[1])],
        [_row(ids[0]), _row(ids[1])],
    )
    fused = rrf_fuse_legs(legs, k=2)
    assert [r.chunk_id for r in fused] == [UUID(ids[1]), UUID(min(ids[0], ids[2], key=UUID))]
    # ids[1] fuses both legs (1/62 + 1/62); ids[0] and ids[2] tie at 1/61.
    assert fused[0].score == 1.0 / 62 + 1.0 / 62
