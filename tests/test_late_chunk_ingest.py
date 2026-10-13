"""Offline tests for late-chunking document-ingest wiring (no latechunk extra).

CI never installs the latechunk extra, so nothing here touches
torch/transformers. THE PURE CORE IS THE POINT: the pooling / windowing /
char-span mapping math lives in ``late_chunk_core`` (plain lists) and is
tested directly through a deterministic, CONTEXT-SENSITIVE fake token
embedder — hash rows depend on the whole window text, which is exactly the
property late chunking buys (context leaks into chunk vectors). The
embedder-selection truth table and the embed_chunks integration
(``embedding_mode`` / ``embedding_note`` on the run meta) reuse the same
fakes; the real ``LateChunkEmbedder`` stays behind the ``latechunk`` extra.
"""

from __future__ import annotations

import hashlib
import itertools
import math
from pathlib import Path

import pytest

from corpus_kb._setup.validate_configs import (
    RUNTIME_CONFIG_KEYS,
    load_yaml,
    validate_runtime_config,
)
from corpus_kb.rag import embedder_selection
from corpus_kb.rag.embedder import l2_normalize
from corpus_kb.rag.embedder_selection import (
    LATE_CHUNKING_MODE,
    POOLED_MODE,
    IngestEmbeddingPlan,
    late_chunking_requested,
    select_ingest_embedder,
)
from corpus_kb.rag.embedders.late_chunk_core import (
    CHARS_PER_TOKEN,
    LateChunkSpan,
    build_document_chunk_vectors,
    char_to_token_spans,
    merge_poolings,
    plan_windows,
    pool_token_rows,
    window_text,
)
from corpus_kb.tools import ingest_common
from corpus_kb.tools.ingest_common import embed_chunks
from corpus_kb.utils.models import Chunk

ROOT = Path(__file__).resolve().parent.parent

CHUNK = "the answer is forty-two. "


# ============================================================================
# FakeTokenEmbedder: deterministic, context-sensitive token vectors
# ============================================================================


class FakeTokenEmbedder:
    """Hash-based token-level embedder satisfying the document-path surface.

    ``embed_window`` returns one special prefix row + one row per 4-char
    token + one special suffix row (mirroring ``special_prefix_tokens=1``).
    Rows hash (window_text, token_index, token_text): hidden states depend
    on the WHOLE window, so identical chunk texts at different positions
    pool to different vectors — the late-chunking property. ``embed_batch``
    is the naive pooled arm: a hash of the text alone, identical for
    identical texts.
    """

    dimensions = 8

    def tokenize(self, text: str) -> list[str]:
        return [text[i : i + 4] for i in range(0, len(text), 4)]

    def token_count(self, text: str) -> int:
        return len(self.tokenize(text))

    def _row(self, *parts: object) -> list[float]:
        digest = hashlib.sha256("|".join(str(p) for p in parts).encode("utf-8")).digest()
        return l2_normalize([(b / 255.0) * 2.0 - 1.0 for b in digest[: self.dimensions]])

    def embed_window(self, text: str) -> list[list[float]]:
        tokens = self.tokenize(text)
        rows = [self._row("<prefix>", text)]
        rows += [self._row(text, i, token) for i, token in enumerate(tokens)]
        rows.append(self._row("<suffix>", text))
        return rows

    def embed(self, text: str) -> list[float]:
        return self._row("pooled", text)

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        return [self.embed(text) for text in texts]

    def instruct(self, text: str) -> str:
        return text


def _spans_provider(fake: FakeTokenEmbedder, window_tokens: int = 64, stride_tokens: int = 8):
    """Bind the fake into the pure core — the seam LateChunkEmbedder uses."""

    def provide(document_text: str, chunk_char_spans: list[tuple[int, int]]) -> list[LateChunkSpan]:
        return build_document_chunk_vectors(
            document_text=document_text,
            chunk_char_spans=chunk_char_spans,
            n_tokens=fake.token_count(document_text),
            token_offsets=None,
            window_forward=fake.embed_window,
            window_tokens=window_tokens,
            stride_tokens=stride_tokens,
        )

    return provide


def _unit(vec: list[float]) -> bool:
    return abs(math.sqrt(sum(x * x for x in vec)) - 1.0) < 1e-9


# ============================================================================
# Window planning
# ============================================================================


def test_plan_windows_fast_path_and_empty():
    assert plan_windows(0, 16, 2) == []
    assert plan_windows(5, 16, 2) == [(0, 5)]
    assert plan_windows(16, 16, 2) == [(0, 16)]


def test_plan_windows_slides_with_stride_overlap():
    windows = plan_windows(40, 10, 3)
    assert windows[0] == (0, 10)
    assert windows[-1] == (35, 40)
    for (_, hi), (next_lo, _) in itertools.pairwise(windows):
        assert next_lo == hi - 3, "stride overlap honored"
    covered: list[int] = []
    for lo, hi in windows:
        covered.extend(range(lo, hi))
    assert set(covered) == set(range(40)), "every token covered by >=1 window"


def test_document_larger_than_window_covers_every_span():
    fake = FakeTokenEmbedder()
    doc = " ".join(f"word{i:02d} filler" for i in range(30))  # >> window tokens
    spans = [(0, 40), (60, 100), (doc.index("word15") - 10, doc.index("word15") + 30)]
    windows = plan_windows(fake.token_count(doc), 12, 4)
    assert len(windows) > 1
    result = build_document_chunk_vectors(
        document_text=doc,
        chunk_char_spans=spans,
        n_tokens=fake.token_count(doc),
        token_offsets=None,
        window_forward=fake.embed_window,
        window_tokens=12,
        stride_tokens=4,
    )
    assert len(result) == len(spans)
    assert all(span.windows_used >= 1 for span in result)
    assert max(span.windows_used for span in result) >= 2, "middle chunks span >1 window"
    assert all(_unit(span.vector) for span in result)


def test_forward_gets_exact_window_slices():
    doc = "aaaa bbbb cccc"
    offsets = [(0, 4), (5, 9), (10, 14)]
    assert window_text(doc, offsets, 0, 2) == "aaaa bbbb"
    assert window_text(doc, offsets, 2, 3) == "cccc"
    assert window_text(doc, None, 1, 2) == doc[1 * CHARS_PER_TOKEN : 2 * CHARS_PER_TOKEN]


# ============================================================================
# char -> token mapping (incl. multi-byte consistency)
# ============================================================================


def test_char_to_token_spans_precise_mapping():
    offsets = [(0, 5), (5, 11), (11, 12)]
    assert char_to_token_spans([(0, 5)], offsets, 12) == [(0, 1)]
    assert char_to_token_spans([(6, 11)], offsets, 12) == [(1, 2)]
    assert char_to_token_spans([(0, 12)], offsets, 12) == [(0, 3)]
    assert char_to_token_spans([(7, 9)], offsets, 12) == [(1, 2)], "interior slice"


def test_char_to_token_spans_heuristic():
    assert char_to_token_spans([(0, 8)], None, 100) == [(0, 2)]
    assert char_to_token_spans([(4, 12)], None, 100) == [(1, 3)]


def test_char_to_token_spans_multibyte_characters_are_char_indexed():
    # Python indices are code points, so multi-byte characters (é, emoji)
    # must never shift the mapping: spans pin CHAR positions.
    doc = "héllo 🎈 wörld"
    offsets = [(0, 5), (6, 7), (8, 13)]
    assert len(doc) == 13
    assert char_to_token_spans([(6, 7)], offsets, len(doc)) == [(1, 2)]
    assert char_to_token_spans([(8, 13)], offsets, len(doc)) == [(2, 3)]
    heuristic = char_to_token_spans([(6, 7)], None, len(doc))
    assert heuristic == [(1, 1)]
    # The heuristic span still pools (degenerate spans clamp to one token).
    result = build_document_chunk_vectors(
        document_text=doc,
        chunk_char_spans=[(6, 7)],
        n_tokens=3,
        token_offsets=offsets,
        window_forward=FakeTokenEmbedder().embed_window,
        window_tokens=16,
        stride_tokens=4,
    )
    assert result[0].windows_used == 1


def test_empty_document_emits_degraded_marker():
    result = build_document_chunk_vectors(
        document_text="",
        chunk_char_spans=[(0, 0)],
        n_tokens=0,
        token_offsets=None,
        window_forward=FakeTokenEmbedder().embed_window,
        window_tokens=16,
        stride_tokens=4,
    )
    assert result[0].vector == []
    assert result[0].windows_used == 0


# ============================================================================
# Span pooling / merging (house normalization contract)
# ============================================================================


def test_pool_token_rows_means_and_normalizes():
    rows = [[1.0, 0.0], [3.0, 0.0], [0.0, 5.0]]
    assert pool_token_rows(rows, 0, 2) == pytest.approx([1.0, 0.0])
    assert _unit(pool_token_rows(rows, 0, 3))


def test_pool_token_rows_clamps_and_zero_noops():
    rows = [[0.0, 0.0], [0.0, 0.0]]
    assert pool_token_rows(rows, 0, 2) == [0.0, 0.0]
    assert pool_token_rows([], 0, 2) == []
    assert pool_token_rows([[1.0, 1.0]], -5, 99) == pytest.approx(l2_normalize([1.0, 1.0]))


def test_merge_poolings_single_identity_and_pair_renormalized():
    unit_x = l2_normalize([3.0, 0.0])
    unit_y = l2_normalize([0.0, 3.0])
    assert merge_poolings([unit_x]) == unit_x
    assert merge_poolings([unit_x, unit_y]) == pytest.approx(l2_normalize([0.5, 0.5]))
    assert merge_poolings([]) == []
    assert merge_poolings([[0.0, 0.0], [0.0, 0.0]]) == [0.0, 0.0]


# ============================================================================
# THE late-chunking property: context leaks in, pooled stays identical
# ============================================================================


def test_identical_chunks_at_different_positions_diverge_pooled_do_not():
    fake = FakeTokenEmbedder()
    doc = "intro text. " + CHUNK + "filler between the two occurrences. " + CHUNK + "outro."
    start1 = doc.index(CHUNK)
    start2 = doc.index(CHUNK, start1 + 1)
    spans = [(start1, start1 + len(CHUNK)), (start2, start2 + len(CHUNK))]

    late = _spans_provider(fake, window_tokens=4096, stride_tokens=512)(doc, spans)
    assert len(late) == 2
    assert late[0].token_start < late[1].token_start
    assert late[0].vector != late[1].vector, "context leaks in: positions differ"
    assert all(_unit(span.vector) for span in late)

    # Deterministic replay: same document -> byte-identical vectors.
    replay = _spans_provider(fake, window_tokens=4096, stride_tokens=512)(doc, spans)
    assert [span.vector for span in replay] == [span.vector for span in late]

    # Naive pooled encoding is position-blind: identical text -> identical.
    assert fake.embed_batch([CHUNK, CHUNK]) == [fake.embed(CHUNK), fake.embed(CHUNK)]
    assert fake.embed(CHUNK) == fake.embed(CHUNK)


# ============================================================================
# select_ingest_embedder truth table
# ============================================================================


def _ingest_config(model: str = "nomic-embed-text", late_chunking: object = True):
    return {
        "embedding": {
            "provider": "pgml",
            "fallback_provider": "ollama",
            "model": model,
            "dimensions": 8,
            "late_chunking": late_chunking,
        }
    }


def test_late_chunking_requested_default_true():
    assert late_chunking_requested({"embedding": {}}) is True
    assert late_chunking_requested({}) is True
    assert late_chunking_requested(_ingest_config(late_chunking=True)) is True
    # Truthiness semantics (matryoshka-gate style): quoted "true" and 1 count.
    assert late_chunking_requested(_ingest_config(late_chunking="true")) is True
    assert late_chunking_requested(_ingest_config(late_chunking=1)) is True
    assert late_chunking_requested(_ingest_config(late_chunking=False)) is False
    assert late_chunking_requested(_ingest_config(late_chunking=0)) is False
    assert late_chunking_requested(_ingest_config(late_chunking="")) is False


def test_select_flag_off_is_plain_pooled(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(embedder_selection, "is_latechunk_installed", lambda: True)
    plan = select_ingest_embedder(_ingest_config(late_chunking=False))
    assert plan.mode == POOLED_MODE
    assert plan.note is None
    assert plan.spans_provider is None


def test_select_extra_missing_is_pooled_with_note(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(embedder_selection, "is_latechunk_installed", lambda: False)
    plan = select_ingest_embedder(_ingest_config(model="qwen3-embedding:0.6b"))
    assert plan.mode == POOLED_MODE
    assert plan.note is not None
    assert "latechunk extra" in plan.note
    assert plan.spans_provider is None


def test_select_wrong_model_is_pooled_with_note(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(embedder_selection, "is_latechunk_installed", lambda: True)
    plan = select_ingest_embedder(_ingest_config(model="nomic-embed-text"))
    assert plan.mode == POOLED_MODE
    assert plan.note is not None
    assert "nomic-embed-text" in plan.note
    assert "token-level-capable" in plan.note
    assert plan.spans_provider is None


def test_select_happy_path_is_structural_not_real(monkeypatch: pytest.MonkeyPatch):
    """The installed+qwen3 path is asserted via the factory seam — no HF load."""
    monkeypatch.setattr(embedder_selection, "is_latechunk_installed", lambda: True)

    class _StubLateChunker(FakeTokenEmbedder):
        def embed_document_chunks(
            self, document_text: str, chunk_char_spans: list[tuple[int, int]]
        ) -> list[LateChunkSpan]:
            raise AssertionError("not called during selection")

    stub = _StubLateChunker()
    plan = select_ingest_embedder(
        _ingest_config(model="qwen3-embedding:0.6b"), late_chunk_factory=lambda: stub
    )
    assert plan.mode == LATE_CHUNKING_MODE
    assert plan.note is None
    assert plan.embedder is stub
    assert plan.spans_provider is not None


def test_select_factory_failure_is_honest_pooled(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(embedder_selection, "is_latechunk_installed", lambda: True)

    def _boom() -> object:
        raise RuntimeError("offline model fetch")

    plan = select_ingest_embedder(
        _ingest_config(model="qwen3-embedding:0.6b"), late_chunk_factory=_boom
    )
    assert plan.mode == POOLED_MODE
    assert plan.note is not None
    assert "failed to load" in plan.note
    assert "offline model fetch" in plan.note


# ============================================================================
# embed_chunks integration (fake-pool pattern from test_ingest_common)
# ============================================================================


class _FakePgmlConn:
    def __init__(self, dimensions: int = 8, value: float = 0.5) -> None:
        self.dimensions = dimensions
        self.value = value

    async def fetch(self, sql: str, model: str, texts: list[str]):
        return [{"embed": [self.value] * self.dimensions} for _ in texts]


class _FakePool:
    def __init__(self, conn: _FakePgmlConn) -> None:
        self._conn = conn

    def acquire(self) -> _FakePool:
        return self

    async def __aenter__(self) -> _FakePgmlConn:
        return self._conn

    async def __aexit__(self, *args: object) -> bool:
        return False


def _doc_with_two_chunks() -> tuple[str, list[tuple[int, int]]]:
    doc = "intro text. " + CHUNK + "filler between the two occurrences. " + CHUNK + "outro."
    start1 = doc.index(CHUNK)
    start2 = doc.index(CHUNK, start1 + 1)
    return doc, [(start1, start1 + len(CHUNK)), (start2, start2 + len(CHUNK))]


async def test_embed_chunks_pooled_mode_meta_contract(monkeypatch: pytest.MonkeyPatch):
    """Flag off -> byte-identical pooled result; meta carries mode only."""
    monkeypatch.setattr(embedder_selection, "is_latechunk_installed", lambda: False)
    config = _ingest_config(late_chunking=False)
    config["embedding"]["provider"] = "pgml"
    chunks = [Chunk(chunk_id="c1", document_id="d1", text="hello", source_type="text")]
    meta: dict[str, object] = {}

    degraded, error = await embed_chunks(chunks, config, pool=_FakePool(_FakePgmlConn()), meta=meta)

    assert degraded is False
    assert error is None
    assert chunks[0].embedding == [0.5] * 8
    assert meta["embedding_mode"] == POOLED_MODE
    assert "embedding_note" not in meta


async def test_embed_chunks_flag_on_nomic_attaches_fallback_note(
    monkeypatch: pytest.MonkeyPatch,
):
    """Default-on flag + nomic model + doc text: pooled runs, note names the model."""
    monkeypatch.setattr(embedder_selection, "is_latechunk_installed", lambda: True)
    config = _ingest_config(model="nomic-embed-text")
    config["embedding"]["provider"] = "pgml"
    chunks = [Chunk(chunk_id="c1", document_id="d1", text="hello", source_type="text")]
    meta: dict[str, object] = {}

    degraded, error = await embed_chunks(
        chunks, config, pool=_FakePool(_FakePgmlConn()), document_text="hello", meta=meta
    )

    assert degraded is False
    assert error is None
    assert chunks[0].embedding == [0.5] * 8
    assert meta["embedding_mode"] == POOLED_MODE
    note = meta.get("embedding_note")
    assert isinstance(note, str)
    assert "nomic-embed-text" in note
    assert "token-level-capable" in note


async def test_embed_chunks_late_mode_embeds_via_document_spans(
    monkeypatch: pytest.MonkeyPatch,
):
    fake = FakeTokenEmbedder()
    doc, spans = _doc_with_two_chunks()
    plan = IngestEmbeddingPlan(
        mode=LATE_CHUNKING_MODE, embedder=fake, spans_provider=_spans_provider(fake)
    )
    monkeypatch.setattr(ingest_common, "select_ingest_embedder", lambda config, pool=None: plan)
    chunks = [
        Chunk(
            chunk_id="c1",
            document_id="d1",
            text=CHUNK,
            source_type="text",
            source_start_char=spans[0][0],
            source_end_char=spans[0][1],
        ),
        Chunk(
            chunk_id="c2",
            document_id="d1",
            text=CHUNK,
            source_type="text",
            source_start_char=spans[1][0],
            source_end_char=spans[1][1],
        ),
    ]
    meta: dict[str, object] = {}

    degraded, error = await embed_chunks(
        chunks,
        _ingest_config(model="qwen3-embedding:0.6b"),
        document_text=doc,
        text_override={"c1": "contextualized blurb IGNORED for span chunks"},
        meta=meta,
    )

    assert degraded is False
    assert error is None
    expected = _spans_provider(fake)(doc, spans)
    assert chunks[0].embedding == expected[0].vector
    assert chunks[1].embedding == expected[1].vector
    assert chunks[0].embedding != chunks[1].embedding
    assert meta["embedding_mode"] == LATE_CHUNKING_MODE
    assert meta.get("embedding_note") is None


async def test_embed_chunks_late_mode_spanless_chunk_falls_back_with_note(
    monkeypatch: pytest.MonkeyPatch,
):
    fake = FakeTokenEmbedder()
    doc, spans = _doc_with_two_chunks()
    plan = IngestEmbeddingPlan(
        mode=LATE_CHUNKING_MODE, embedder=fake, spans_provider=_spans_provider(fake)
    )
    monkeypatch.setattr(ingest_common, "select_ingest_embedder", lambda config, pool=None: plan)
    chunks = [
        Chunk(
            chunk_id="c1",
            document_id="d1",
            text=CHUNK,
            source_type="text",
            source_start_char=spans[0][0],
            source_end_char=spans[0][1],
        ),
        Chunk(chunk_id="c2", document_id="d1", text=CHUNK, source_type="text"),
    ]
    meta: dict[str, object] = {}

    degraded, error = await embed_chunks(
        chunks, _ingest_config(model="qwen3-embedding:0.6b"), document_text=doc, meta=meta
    )

    assert degraded is False
    assert error is None
    assert chunks[0].embedding == _spans_provider(fake)(doc, [spans[0]])[0].vector
    assert chunks[1].embedding == fake.embed(CHUNK), "spanless chunk embeds pooled"
    note = meta.get("embedding_note")
    assert isinstance(note, str)
    assert "1/2" in note and "source" in note and "char spans" in note
    assert meta["embedding_mode"] == LATE_CHUNKING_MODE


async def test_embed_chunks_late_mode_zero_vectors_degrade(monkeypatch: pytest.MonkeyPatch):
    doc, spans = _doc_with_two_chunks()

    def _zero_spans(
        document_text: str, chunk_char_spans: list[tuple[int, int]]
    ) -> list[LateChunkSpan]:
        return [
            LateChunkSpan(vector=[0.0] * 8, token_start=0, token_end=1, windows_used=1)
            for _ in chunk_char_spans
        ]

    plan = IngestEmbeddingPlan(
        mode=LATE_CHUNKING_MODE, embedder=FakeTokenEmbedder(), spans_provider=_zero_spans
    )
    monkeypatch.setattr(ingest_common, "select_ingest_embedder", lambda config, pool=None: plan)
    chunks = [
        Chunk(
            chunk_id="c1",
            document_id="d1",
            text=CHUNK,
            source_type="text",
            source_start_char=spans[0][0],
            source_end_char=spans[0][1],
        )
    ]
    meta: dict[str, object] = {}

    degraded, error = await embed_chunks(
        chunks, _ingest_config(model="qwen3-embedding:0.6b"), document_text=doc, meta=meta
    )

    assert degraded is True
    assert error == "Connection failed: LateChunkEmbedder returned zero vectors"
    assert chunks[0].embedding == [0.0] * 8
    assert meta["embedding_mode"] == LATE_CHUNKING_MODE


async def test_embed_chunks_late_mode_backend_failure_reports_error(
    monkeypatch: pytest.MonkeyPatch,
):
    doc, spans = _doc_with_two_chunks()

    def _boom(document_text: str, chunk_char_spans: list[tuple[int, int]]) -> list[LateChunkSpan]:
        raise RuntimeError("forward pass exploded")

    plan = IngestEmbeddingPlan(
        mode=LATE_CHUNKING_MODE, embedder=FakeTokenEmbedder(), spans_provider=_boom
    )
    monkeypatch.setattr(ingest_common, "select_ingest_embedder", lambda config, pool=None: plan)
    chunks = [
        Chunk(
            chunk_id="c1",
            document_id="d1",
            text=CHUNK,
            source_type="text",
            source_start_char=spans[0][0],
            source_end_char=spans[0][1],
        )
    ]

    degraded, error = await embed_chunks(
        chunks, _ingest_config(model="qwen3-embedding:0.6b"), document_text=doc
    )

    assert degraded is True
    assert error == "RuntimeError: forward pass exploded"
    assert chunks[0].embedding is None


async def test_embed_chunks_late_requested_without_document_text_pools(
    monkeypatch: pytest.MonkeyPatch,
):
    """No document text (reembed repair path): honest pooled fallback, no crash."""
    monkeypatch.setattr(embedder_selection, "is_latechunk_installed", lambda: False)
    config = _ingest_config(model="qwen3-embedding:0.6b")
    config["embedding"]["provider"] = "pgml"
    chunks = [Chunk(chunk_id="c1", document_id="d1", text="hello", source_type="text")]
    meta: dict[str, object] = {}

    degraded, error = await embed_chunks(chunks, config, pool=_FakePool(_FakePgmlConn()), meta=meta)

    assert degraded is False
    assert error is None
    assert chunks[0].embedding == [0.5] * 8
    assert meta["embedding_mode"] == POOLED_MODE
    note = meta.get("embedding_note")
    assert isinstance(note, str)
    assert "no document text" in note


async def test_embed_chunks_empty_chunks_short_circuits():
    meta: dict[str, object] = {}
    degraded, error = await embed_chunks([], _ingest_config(), meta=meta)
    assert degraded is False
    assert error is None
    assert meta["embedding_mode"] == POOLED_MODE


# ============================================================================
# validate_configs: the embedding.late_chunking runtime key
# ============================================================================


def test_runtime_config_keys_declare_late_chunking():
    assert RUNTIME_CONFIG_KEYS["embedding.late_chunking"] is bool


def _runtime_config(late_chunking: object = True) -> dict[str, object]:
    return {
        "search": {
            "exclude_degraded": True,
            "qa_context_in_fts": False,
            "matryoshka_enabled": False,
        },
        "embedding": {"mrl_trained": False, "late_chunking": late_chunking},
        "ingestion": {"default": "default", "strategies": {}},
    }


def test_validate_runtime_config_accepts_late_chunking_bool():
    assert validate_runtime_config(_runtime_config(True)) == []
    assert validate_runtime_config(_runtime_config(False)) == []


def test_validate_runtime_config_rejects_mistyped_and_missing_late_chunking():
    errors = validate_runtime_config(_runtime_config("true"))
    assert any("key 'embedding.late_chunking' must be bool" in e for e in errors)
    missing = _runtime_config()
    del missing["embedding"]["late_chunking"]
    errors = validate_runtime_config(missing)
    assert any("missing required key 'embedding.late_chunking'" in e for e in errors)


def test_shipped_config_yamls_declare_late_chunking_true():
    for rel in ("config.yaml", "src/corpus_kb/config.yaml"):
        cfg = load_yaml(ROOT / rel)
        embedding = cfg["embedding"]
        assert embedding["late_chunking"] is True, rel
