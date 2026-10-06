"""Offline tests for the late-chunking embedder + G1 promotion guard (todo 13).

These run WITHOUT the latechunk extra (CI has no sentence-transformers):
the shim module imports cleanly, the import guard raises informatively, and
the pooling / windowing / normalization math is exercised through pure
python fakes of the typed tensor Protocols.
"""

from __future__ import annotations

import math

import pytest

from corpus_kb.rag.embedder import (
    aembed_batch,
    assert_unit_or_zero,
    l2_normalize,
)
from corpus_kb.rag.embedders import (
    LateChunkDependencyError,
    LateChunkEmbedder,
    is_latechunk_installed,
)
from corpus_kb.rag.embedders.late_chunk import macro_windows, pool_span
from corpus_kb.research.promotion import (
    PROMOTABLE_DIMENSIONS,
    NonPromotableDimensionsError,
    assert_promotable,
)


class _FakeTensor1D:
    def __init__(self, values: list[float]) -> None:
        self.values = values

    def tolist(self) -> list[float]:
        return list(self.values)


class _FakeTensor2D:
    def __init__(self, rows: list[list[float]]) -> None:
        self.rows = rows

    def mean(self, dim: int, keepdim: bool = False) -> _FakeTensor1D:
        assert dim == 0
        n = len(self.rows)
        return _FakeTensor1D([sum(col) / n for col in zip(*self.rows, strict=True)])


class FakeHidden:
    """Pure-python stand-in for a (1, seq_len, hidden) token-states tensor."""

    def __init__(self, rows: list[list[float]]) -> None:
        self.rows = rows

    def __getitem__(self, key: tuple) -> _FakeTensor2D:
        _batch, row_slice, col_slice = key
        picked = [row[col_slice] for row in self.rows[row_slice]]
        return _FakeTensor2D(picked)


# -- import guard (proves the extras-less CI surface) ------------------------


def test_shim_imports_cleanly_without_extra():
    from corpus_kb.rag.embedders import _latechunk_types

    assert _latechunk_types.LateChunkDependencyError is not None


@pytest.mark.skipif(is_latechunk_installed(), reason="latechunk installed; guard unreachable")
def test_import_guard_raises_informative_error():
    with pytest.raises(LateChunkDependencyError, match=r"pip install.*latechunk"):
        LateChunkEmbedder()


@pytest.mark.skipif(is_latechunk_installed(), reason="latechunk installed; guard unreachable")
def test_shim_imports_raise_informatively():
    from corpus_kb.rag.embedders._latechunk_types import (
        import_sentence_transformers,
        import_torch,
        import_transformers,
    )

    for import_fn in (import_transformers, import_sentence_transformers, import_torch):
        with pytest.raises(LateChunkDependencyError):
            import_fn()


def test_embedder_protocol_shape():
    for member in ("dimensions", "embed", "embed_batch", "instruct"):
        assert hasattr(LateChunkEmbedder, member), member


# -- normalization boundary contract (r7/r8) ---------------------------------


def test_l2_normalize_unit_and_zero_noop():
    assert l2_normalize([3.0, 4.0]) == pytest.approx([0.6, 0.8])
    assert l2_normalize([0.0, 0.0, 0.0]) == [0.0, 0.0, 0.0]


def test_assert_unit_or_zero_noops_on_zero_vector():
    assert_unit_or_zero([0.0] * 8)
    assert_unit_or_zero([1.0] + [0.0] * 7)
    with pytest.raises(ValueError, match="unit-norm"):
        assert_unit_or_zero([1.0, 1.0])


# -- macro windowing (v5 5.3: turn boundaries, ~10% overlap) -----------------


def test_macro_windows_empty_and_single():
    assert macro_windows([]) == []
    assert macro_windows([7]) == [(0, 1)]


def test_macro_windows_fit_within_max_tokens(monkeypatch: pytest.MonkeyPatch):
    from corpus_kb.rag.embedders import late_chunk

    monkeypatch.setattr(late_chunk, "MAX_MACRO_TOKENS", 25)
    windows = macro_windows([10, 10, 10, 10, 10])
    assert windows[0] == (0, 2)
    for lo, hi in windows:
        assert lo < hi
        assert hi <= 5


def test_macro_windows_small_corpus_single_window():
    tokens = [12] * 26
    assert macro_windows(tokens) == [(0, 26)]


def test_macro_windows_overlap_is_about_ten_percent():
    from corpus_kb.rag.embedders import late_chunk

    monkey = pytest.MonkeyPatch()
    try:
        monkey.setattr(late_chunk, "MAX_MACRO_TOKENS", 50)
        windows = macro_windows([1] * 100)
    finally:
        monkey.undo()
    assert windows[0] == (0, 50)
    assert windows[1][0] == 45, "second window starts ~10% (5 turns) before the first ends"
    assert windows[-1][1] == 100


def test_macro_windows_progress_with_giant_single_turn(monkeypatch: pytest.MonkeyPatch):
    from corpus_kb.rag.embedders import late_chunk

    monkeypatch.setattr(late_chunk, "MAX_MACRO_TOKENS", 25)
    windows = macro_windows([1000, 1000])
    assert windows == [(0, 1), (1, 2)]


# -- span pooling over token states -------------------------------------------


def test_pool_span_means_over_token_range_and_normalizes():
    hidden = FakeHidden([[1.0, 0.0], [1.0, 0.0], [3.0, 0.0], [5.0, 0.0]])
    vec = pool_span(hidden, 1, 3, 4)
    assert vec == pytest.approx([1.0, 0.0])
    assert abs(math.sqrt(sum(x * x for x in vec)) - 1.0) < 1e-9


def test_pool_span_zero_vector_noops():
    hidden = FakeHidden([[0.0, 0.0], [0.0, 0.0]])
    assert pool_span(hidden, 0, 2, 2) == [0.0, 0.0]


def test_pool_span_past_truncation_pools_final_token():
    hidden = FakeHidden([[1.0, 0.0], [0.0, 2.0]])
    assert pool_span(hidden, 9, 9, 2) == pytest.approx([0.0, 1.0])


# -- aembed_batch accepts a structural Embedder (todo-13 protocol arm) --------


class _ProtocolDouble:
    dimensions = 4

    def embed(self, text: str) -> list[float]:
        return [0.0] * 4

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        return [[0.0] * 4 for _ in texts]

    def instruct(self, text: str) -> str:
        return text


async def test_aembed_batch_accepts_protocol_embedder():
    out = await aembed_batch(_ProtocolDouble(), ["a", "b"])
    assert out == [[0.0] * 4, [0.0] * 4]


# -- promotion guard (r5: non-1024 winner HALTS) ------------------------------


def test_promotable_dimensions_constant():
    assert PROMOTABLE_DIMENSIONS == 1024


def test_assert_promotable_accepts_1024():
    assert_promotable(1024)


def test_assert_promotable_halts_on_non_1024():
    with pytest.raises(NonPromotableDimensionsError, match="halt"):
        assert_promotable(768)
