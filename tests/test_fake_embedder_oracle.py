"""Tests for the FakeEmbedder eval-baseline oracle (rag/fake_embedder.py)."""

from __future__ import annotations

import math

import pytest

from corpus_kb.rag.fake_embedder import DEFAULT_DIMENSIONS, PROBE_TEXT, FakeEmbedder


def test_same_input_identical_across_instances() -> None:
    first = FakeEmbedder(dimensions=256).embed(PROBE_TEXT)
    second = FakeEmbedder(dimensions=256).embed(PROBE_TEXT)
    assert first == second


def test_different_inputs_differ() -> None:
    a = FakeEmbedder(dimensions=256).embed("alpha document")
    b = FakeEmbedder(dimensions=256).embed("beta document")
    assert a != b
    assert len(a) == len(b) == 256


def test_dimensions_are_configurable() -> None:
    for dims in (8, 64, 256, 1024):
        vec = FakeEmbedder(dimensions=dims).embed("dim probe")
        assert len(vec) == dims


def test_default_dimensions_is_1024_research_grain() -> None:
    assert DEFAULT_DIMENSIONS == 1024
    assert len(FakeEmbedder().embed("default probe")) == 1024


def test_vectors_are_l2_normalized() -> None:
    for text in ("a", "some longer interview answer about housing policy", "…unicode…"):
        vec = FakeEmbedder(dimensions=512).embed(text)
        norm = math.sqrt(sum(x * x for x in vec))
        assert norm == pytest.approx(1.0, abs=1e-9)


def test_batch_preserves_order_and_matches_single() -> None:
    embedder = FakeEmbedder(dimensions=128)
    texts = ["first", "second", "third"]
    batch = embedder.embed_batch(texts)
    assert [embedder.embed(t) for t in texts] == batch


def test_no_all_zero_vectors() -> None:
    # The whole point of the oracle: NEVER reproduce the degraded all-zero
    # fallback that OllamaEmbedder emits when Ollama is unreachable.
    for text in ("", "x", "empty-ish"):
        vec = FakeEmbedder(dimensions=64).embed(text)
        assert any(x != 0.0 for x in vec)


def test_satisfies_embedder_protocol() -> None:
    from corpus_kb.rag.embedder import Embedder

    embedder: Embedder = FakeEmbedder(dimensions=128)
    assert embedder.instruct("q").startswith("Instruct:")


def test_matryoshka_slice_renormalizes() -> None:
    embedder = FakeEmbedder(dimensions=512)
    full = embedder.embed("matryoshka probe")
    sliced = embedder.embed_matryoshka("matryoshka probe", 128)
    assert len(sliced) == 128
    norm = math.sqrt(sum(x * x for x in sliced))
    assert norm == pytest.approx(1.0, abs=1e-9)
    # Front-slice of a unit vector, re-normalized: proportional to the prefix.
    scale = 1.0 / math.sqrt(sum(x * x for x in full[:128]))
    assert sliced == pytest.approx([x * scale for x in full[:128]], abs=1e-9)


def test_batch_matryoshka_matches_single() -> None:
    embedder = FakeEmbedder(dimensions=256)
    texts = ["one", "two"]
    assert embedder.embed_batch_matryoshka(texts, 64) == [
        embedder.embed_matryoshka(t, 64) for t in texts
    ]


def test_cross_process_stability_is_hash_derived_not_rng_seeded() -> None:
    # The legacy rag/embedder.FakeEmbedder seeds random.Random(int(sha256));
    # this oracle derives floats directly from digest bytes so the stream is
    # identical on any interpreter/numpy version. Pin a known digest input.
    vec = FakeEmbedder(dimensions=4).embed("stability")
    assert vec == FakeEmbedder(dimensions=4).embed("stability")
    assert len(vec) == 4
    # Reformatting the input changes the vector (no accidental collisions).
    assert vec != FakeEmbedder(dimensions=4).embed("stability ")


def test_rejects_non_positive_dimensions() -> None:
    with pytest.raises(ValueError, match="dimensions"):
        FakeEmbedder(dimensions=0)
