"""Extras-less coverage for the LlamaIndex backend success paths.

CI never installs the ``[llamaindex]`` extra, so the extra-present code
in ``storage/llamaindex_backend.py`` must be reachable through the
``_import_module`` seam with module-shaped fakes. The coverage-diff gate
counts changed lines on the ubuntu leg, and that leg has no llama-index
(the #485 pattern: changed lines need tests that run on every leg).
"""

from __future__ import annotations

import json
from types import ModuleType
from typing import Any, ClassVar

import pytest

from corpus_kb.storage import llamaindex_backend as lib


def _fake_module(name: str, **attrs: object) -> ModuleType:
    """Build one importable-looking module with the given attributes."""
    module = ModuleType(name)
    for key, value in attrs.items():
        setattr(module, key, value)
    return module


class _RecordingEmbedding:
    """Stands in for llama_index.embeddings.ollama.OllamaEmbedding."""

    created: ClassVar[list[dict[str, Any]]] = []

    def __init__(self, **kwargs: Any) -> None:
        self.kwargs = kwargs
        type(self).created.append(kwargs)

    async def aget_text_embedding(self, text: str) -> list[float]:
        return [0.0]


class _FakePGVectorStore:
    """Stands in for llama_index.vector_stores.postgres.PGVectorStore."""

    last_from_params: ClassVar[dict[str, Any]] = {}

    @classmethod
    def from_params(cls, **kwargs: Any) -> _FakePGVectorStore:
        cls.last_from_params = kwargs
        store = cls()
        store.embed_dim = int(kwargs.get("embed_dim", 768))
        return store

    async def adelete(self, ref_doc_id: str) -> object:
        return None


class _FakeTextNode:
    """Stands in for llama_index.core.schema.TextNode."""

    def __init__(self, **kwargs: Any) -> None:
        self.id_ = kwargs["id_"]
        self.text = kwargs["text"]
        self.metadata = kwargs["metadata"]
        self.relationships: dict[object, object] = {}


class _FakeNodeRelationship:
    SOURCE = "SOURCE"


class _FakeRelatedNodeInfo:
    def __init__(self, node_id: str) -> None:
        self.node_id = node_id


class _FakeRetriever:
    def __init__(self, nodes: list[Any]) -> None:
        self._nodes = nodes
        self.similarity_top_k = 0

    def as_retriever(self, similarity_top_k: int) -> _FakeRetriever:
        self.similarity_top_k = similarity_top_k
        return self

    async def aretrieve(self, query: str) -> list[Any]:
        return self._nodes


class _FakeVectorStoreIndex:
    """Stands in for llama_index.core.VectorStoreIndex."""

    last_from_vector_store: ClassVar[dict[str, Any]] = {}
    inserted_nodes: ClassVar[list[Any]] = []

    @classmethod
    def from_vector_store(cls, **kwargs: Any) -> _FakeVectorStoreIndex:
        cls.last_from_vector_store = kwargs
        return cls()

    def as_retriever(self, similarity_top_k: int) -> _FakeRetriever:
        return _FakeRetriever([])

    async def async_insert_nodes(self, nodes: list[Any]) -> None:
        type(self).inserted_nodes.extend(nodes)


@pytest.fixture
def _fake_llamaindex(monkeypatch: pytest.MonkeyPatch) -> None:
    """Point the import seam at module-shaped fakes for every llama_index name."""
    schema = _fake_module(
        "llama_index.core.schema",
        TextNode=_FakeTextNode,
        NodeRelationship=_FakeNodeRelationship,
        RelatedNodeInfo=_FakeRelatedNodeInfo,
    )
    core = _fake_module(
        "llama_index.core",
        VectorStoreIndex=_FakeVectorStoreIndex,
        schema=schema,
    )
    embeddings = _fake_module(
        "llama_index.embeddings.ollama",
        OllamaEmbedding=_RecordingEmbedding,
    )
    pgvector = _fake_module(
        "llama_index.vector_stores.postgres",
        PGVectorStore=_FakePGVectorStore,
    )
    modules = {
        "llama_index.core": core,
        "llama_index.core.schema": schema,
        "llama_index.embeddings.ollama": embeddings,
        "llama_index.vector_stores.postgres": pgvector,
    }
    monkeypatch.setattr(lib, "_import_module", modules.__getitem__)


def _config(dimensions: int) -> dict:
    return {
        "database": {
            "connection_string": "postgresql://corpus_user:corpus_pass@localhost:5432/corpus_kb"
        },
        "embedding": {
            "model": "nomic-embed-text",
            "base_url": "http://localhost:11434",
            "dimensions": dimensions,
        },
    }


def test_the_import_seam_answers_present_modules() -> None:
    """The seam body imports a real module when the name resolves."""
    assert lib._import_module("json") is json


def test_construction_builds_the_embedding_client(
    _fake_llamaindex: None,
) -> None:
    """The constructor loads the Ollama embedding client through the seam."""
    _RecordingEmbedding.created.clear()
    backend = lib.LlamaIndexPostgresBackend(_config(768))
    assert backend._embed_model == "nomic-embed-text"
    assert backend._embed_dimensions == 768
    assert _RecordingEmbedding.created == [
        {
            "model_name": "nomic-embed-text",
            "base_url": "http://localhost:11434",
            "embed_batch_size": 1,
        }
    ]


@pytest.mark.asyncio
async def test_initialize_builds_the_vector_store(
    _fake_llamaindex: None,
) -> None:
    """initialize() builds the PGVectorStore with the pinned HNSW kwargs."""
    backend = lib.LlamaIndexPostgresBackend(_config(768))
    await backend.initialize()
    assert _FakePGVectorStore.last_from_params["embed_dim"] == 768
    assert _FakePGVectorStore.last_from_params["table_name"] == "corpus_rag_nodes"
    assert _FakePGVectorStore.last_from_params["schema_name"] == "corpus_rag"
    assert _FakePGVectorStore.last_from_params["hnsw_kwargs"]["hnsw_m"] == 16
    assert backend._vector_store is not None
    assert await backend.health() is True


@pytest.mark.asyncio
async def test_ingest_builds_nodes_and_inserts_them(
    _fake_llamaindex: None,
) -> None:
    """ingest() maps chunks to TextNodes and inserts them through the index."""
    backend = lib.LlamaIndexPostgresBackend(_config(768))
    await backend.initialize()
    _FakeVectorStoreIndex.inserted_nodes.clear()
    chunks = [
        {"text": "first", "metadata": {"page": 1}},
        {"text": "second"},
    ]
    await backend.ingest("doc-1", chunks)
    nodes = _FakeVectorStoreIndex.inserted_nodes
    assert [node.id_ for node in nodes] == ["doc-1_0", "doc-1_1"]
    assert [node.text for node in nodes] == ["first", "second"]
    assert nodes[0].metadata["page"] == 1
    assert nodes[1].metadata["chunk_index"] == 1
    source = nodes[0].relationships[_FakeNodeRelationship.SOURCE]
    assert isinstance(source, _FakeRelatedNodeInfo)
    assert source.node_id == "doc-1"
    assert _FakeVectorStoreIndex.last_from_vector_store["vector_store"] is backend._vector_store


@pytest.mark.asyncio
async def test_retrieve_maps_scored_nodes(
    _fake_llamaindex: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """retrieve() maps node id, source, text, score, and metadata."""
    backend = lib.LlamaIndexPostgresBackend(_config(768))
    await backend.initialize()

    class _Node:
        id_ = "n-1"
        text = "hello"
        metadata: ClassVar[dict[str, str]] = {"source_id": "doc-1"}

    class _Scored:
        node = _Node()
        score = 0.5

    retriever = _FakeRetriever([_Scored()])
    monkeypatch.setattr(
        _FakeVectorStoreIndex,
        "as_retriever",
        lambda self, similarity_top_k: retriever.as_retriever(similarity_top_k),
    )
    results = await backend.retrieve("query text", 3)
    assert results == [
        {
            "node_id": "n-1",
            "source_id": "doc-1",
            "text": "hello",
            "score": 0.5,
            "metadata": {"source_id": "doc-1"},
        }
    ]
    assert retriever.similarity_top_k == 3
