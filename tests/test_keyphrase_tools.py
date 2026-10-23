"""The keyphrases op: three variants, honest envelopes, deterministic order.

Real sklearn drives the tfidf path (the venv always has it). The
embedding path runs against a fixed-vector fake embedder plus stored
vectors at the two read seams (``corpus_kb.rag.embedder``,
``classification.engine.read_stored_vectors``). The llm path fakes the
generate worker. Covered: variant/limit validation with remediation,
the empty and unavailable guards, the desc-score/asc-phrase order, the
max_df drop, 6-place rounding, and the llm parse/unreachable paths.
"""

from __future__ import annotations

import itertools
from typing import ClassVar

import pytest

from corpus_kb.errors import ValidationError
from corpus_kb.tools import keyphrase_llm, keyphrase_tools

TENANT = "00000000-0000-0000-0000-000000000001"


class _FakeChunk:
    def __init__(self, chunk_id: str, text: str) -> None:
        self.chunk_id = chunk_id
        self.text = text
        self.metadata: dict[str, object] = {}


def _patch_chunks(monkeypatch: pytest.MonkeyPatch, chunks: list[_FakeChunk]) -> None:
    async def fake_read(pool: object, tenant_id: str, limit: int = 100000) -> list[_FakeChunk]:
        return chunks

    monkeypatch.setattr(keyphrase_tools.labeling_engine, "read_chunks", fake_read)


# A phrase in every document must vanish under max_df 0.9; the rare
# phrase in one document must outscore the rest.
TEXTS = {
    "c1": "budget review notes shared budget line",
    "c2": "budget outlook shared budget forecast",
    "c3": "budget memo shared budget draft",
    "c4": "quasar calibration matrix",
}


async def test_unknown_variant_is_a_validation_error_with_remediation() -> None:
    with pytest.raises(ValidationError) as excinfo:
        await keyphrase_tools.keyphrases_extract(None, tenant_id=TENANT, variant="textrank")
    assert "tfidf" in str(excinfo.value)


async def test_bad_limit_is_a_validation_error() -> None:
    for bad in (0, -3, True):
        with pytest.raises(ValidationError):
            await keyphrase_tools.keyphrases_extract(None, tenant_id=TENANT, limit=bad)


async def test_no_chunks_answer_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_chunks(monkeypatch, [])
    result = await keyphrase_tools.keyphrases_extract(None, tenant_id=TENANT)
    assert result["status"] == "empty"
    assert result["keyphrases"] == []
    assert result["screened_count"] == 0
    assert result["variant"] == "tfidf"
    assert str(result["remedy"])


async def test_tfidf_success_envelope_and_order(monkeypatch: pytest.MonkeyPatch) -> None:
    chunks = [_FakeChunk(cid, text) for cid, text in TEXTS.items()]
    _patch_chunks(monkeypatch, chunks)
    result = await keyphrase_tools.keyphrases_extract(None, tenant_id=TENANT, limit=5)
    assert result["status"] == "success"
    assert result["variant"] == "tfidf"
    assert result["screened_count"] == 4
    rows = result["keyphrases"]
    assert 0 < len(rows) <= 5
    scores = [row["score"] for row in rows]
    assert scores == sorted(scores, reverse=True)
    # desc score, phrase ascending within ties.
    for left, right in itertools.pairwise(rows):
        if left["score"] == right["score"]:
            assert left["phrase"] < right["phrase"]
    # The everywhere phrase is dropped by max_df; the one-doc phrase survives.
    phrases = {row["phrase"] for row in rows}
    assert "shared" not in phrases
    assert "quasar" in phrases
    assert all(row["score"] == round(row["score"], 6) for row in rows)


async def test_tfidf_chunk_id_filter_shrinks_the_denominator(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    chunks = [_FakeChunk(cid, text) for cid, text in TEXTS.items()]
    _patch_chunks(monkeypatch, chunks)
    result = await keyphrase_tools.keyphrases_extract(
        None, tenant_id=TENANT, chunk_ids=["c1", "c4"], limit=10
    )
    assert result["screened_count"] == 2
    assert "quasar" in {row["phrase"] for row in result["keyphrases"]}


class _MapEmbedder:
    """Fixed 2-d vectors for known words; anything else maps to [0, 1]."""

    dimensions = 2

    VECTORS: ClassVar[dict[str, list[float]]] = {
        "space": [1.0, 0.0],
        "rocket": [0.9, 0.1],
        "orbit": [0.8, 0.2],
        "budget": [0.0, 1.0],
        "memo": [0.0, 0.9],
    }

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        return [list(self.VECTORS.get(text, [0.0, 1.0])) for text in texts]


class _ZeroEmbedder:
    dimensions = 2

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        return [[0.0, 0.0] for _ in texts]


def _patch_embedder(monkeypatch: pytest.MonkeyPatch, embedder: object) -> None:
    monkeypatch.setattr(
        "corpus_kb.rag.embedder.create_embedder", lambda config=None, pool=None: embedder
    )


class _FakeStoredVector:
    def __init__(self, chunk_id: str, vector: list[float]) -> None:
        self.chunk_id = chunk_id
        self.text = ""
        self.vector = vector


def _patch_stored_vectors(
    monkeypatch: pytest.MonkeyPatch, vectors: list[_FakeStoredVector]
) -> None:
    async def fake_read(pool: object, tenant_id: str) -> list[_FakeStoredVector]:
        return vectors

    monkeypatch.setattr(keyphrase_tools.classification_engine, "read_stored_vectors", fake_read)


EMBED_TEXTS = {
    "c1": "space rocket launch",
    "c2": "orbit space station",
    "c3": "budget memo draft",
}


async def test_embedding_ranks_by_cosine_to_the_stored_centroid(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    chunks = [_FakeChunk(cid, text) for cid, text in EMBED_TEXTS.items()]
    _patch_chunks(monkeypatch, chunks)
    _patch_embedder(monkeypatch, _MapEmbedder())
    _patch_stored_vectors(
        monkeypatch,
        [
            _FakeStoredVector("c1", [1.0, 0.0]),
            _FakeStoredVector("c2", [1.0, 0.0]),
            _FakeStoredVector("c3", [1.0, 0.0]),
        ],
    )
    result = await keyphrase_tools.keyphrases_extract(
        None, tenant_id=TENANT, variant="embedding", limit=5
    )
    assert result["status"] == "success"
    rows = result["keyphrases"]
    assert rows[0]["phrase"] == "space"
    scores = [row["score"] for row in rows]
    assert scores == sorted(scores, reverse=True)


async def test_embedding_without_stored_vectors_embeds_the_texts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    chunks = [_FakeChunk(cid, text) for cid, text in EMBED_TEXTS.items()]
    _patch_chunks(monkeypatch, chunks)
    embedder = _MapEmbedder()
    _patch_embedder(monkeypatch, embedder)
    _patch_stored_vectors(monkeypatch, [])
    result = await keyphrase_tools.keyphrases_extract(
        None, tenant_id=TENANT, variant="embedding", limit=5
    )
    assert result["status"] == "success"
    # The embedded texts all fall on the default direction, so the
    # budget-aligned candidates sit at the centroid.
    assert result["keyphrases"][0]["phrase"] == "budget"


async def test_embedding_with_a_dead_embedder_answers_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    chunks = [_FakeChunk(cid, text) for cid, text in EMBED_TEXTS.items()]
    _patch_chunks(monkeypatch, chunks)
    _patch_embedder(monkeypatch, _ZeroEmbedder())
    _patch_stored_vectors(monkeypatch, [])
    result = await keyphrase_tools.keyphrases_extract(None, tenant_id=TENANT, variant="embedding")
    assert result["status"] == "unavailable"
    assert "embed" in str(result["reason"]).lower()
    assert str(result["remedy"])
    assert result["keyphrases"] == []


async def test_llm_success_parses_the_json_list(monkeypatch: pytest.MonkeyPatch) -> None:
    chunks = [_FakeChunk(cid, text) for cid, text in EMBED_TEXTS.items()]
    _patch_chunks(monkeypatch, chunks)

    def fake_generate(prompt: str, model: str, base_url: str) -> str:
        assert model == "qwen3"
        return '<think>hm</think>["space rocket", "budget memo", "space rocket", ""]'

    monkeypatch.setattr(keyphrase_llm, "_generate", fake_generate)
    result = await keyphrase_tools.keyphrases_extract(None, tenant_id=TENANT, variant="llm")
    assert result["status"] == "success"
    phrases = [row["phrase"] for row in result["keyphrases"]]
    assert phrases == ["space rocket", "budget memo"]  # deduped, empties dropped
    assert result["keyphrases"][0]["score"] == 0.0  # order is the model's, not a score
    assert result["model"] == "qwen3"
    assert str(result["note"])


async def test_llm_parse_failure_answers_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    chunks = [_FakeChunk(cid, text) for cid, text in EMBED_TEXTS.items()]
    _patch_chunks(monkeypatch, chunks)
    monkeypatch.setattr(keyphrase_llm, "_generate", lambda prompt, model, base_url: "no json here")
    result = await keyphrase_tools.keyphrases_extract(None, tenant_id=TENANT, variant="llm")
    assert result["status"] == "empty"
    assert "json" in str(result["reason"]).lower()


async def test_llm_unreachable_answers_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    chunks = [_FakeChunk(cid, text) for cid, text in EMBED_TEXTS.items()]
    _patch_chunks(monkeypatch, chunks)

    def dead(prompt: str, model: str, base_url: str) -> str:
        raise ConnectionError("refused")

    monkeypatch.setattr(keyphrase_llm, "_generate", dead)
    result = await keyphrase_tools.keyphrases_extract(None, tenant_id=TENANT, variant="llm")
    assert result["status"] == "unavailable"
    assert str(result["remedy"])
