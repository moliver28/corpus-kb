"""LlamaIndex-backed RAG storage using PGVectorStore and Ollama.

This backend is intentionally isolated from the existing PostgresGraphStore
path: it uses PGVectorStore for vector search and Ollama for local embeddings.
It never falls back to OpenAI/Anthropic/cloud providers.

The four llama-index packages live in the optional ``[llamaindex]`` extra.
Every llama_index import runs through the :func:`_load` seam (the marimo
pattern), so a base install never imports llama-index. Construction raises
the typed :class:`LlamaIndexDependencyError` when the extra is absent;
:func:`create_initialized_rag_backend` catches it, logs one honest line,
and boots the server with the mirror disabled.
"""

from __future__ import annotations

import asyncio
import importlib
import logging
from types import ModuleType
from typing import Protocol

from corpus_kb.dimensions import NOMIC_EMBEDDING_DIMENSIONS
from corpus_kb.errors import DependencyMissingError, InternalError, ValidationError

logger = logging.getLogger(__name__)

# Matches the version-guard and doctor probe budget (R2 s7 table).
LLAMAINDEX_PROBE_TIMEOUT_S: float = 3.0

_LLAMAINDEX_REMEDIATION = (
    "The LlamaIndex backend needs llama-index. "
    "Install the extra: pip install 'corpus-kb[llamaindex]'."
)


class LlamaIndexDependencyError(DependencyMissingError):
    """The ``[llamaindex]`` extra is not installed."""

    remediation = _LLAMAINDEX_REMEDIATION


class _EmbeddingClient(Protocol):
    """The OllamaEmbedding surface this backend calls (the latechunk pattern).

    No static llama_index import exists in this package, so the extras-less
    pyright gate resolves without the extra installed.
    """

    async def aget_text_embedding(self, text: str) -> list[float]: ...


class _VectorStoreClient(Protocol):
    """The PGVectorStore surface this backend calls."""

    embed_dim: int

    async def adelete(self, ref_doc_id: str) -> object: ...


class DimensionMismatchError(ValidationError):
    """Raised when configured embedding dimensions do not match the vector store."""


def _import_module(name: str) -> ModuleType:
    """The one import seam (tests substitute a ModuleNotFoundError here)."""
    return importlib.import_module(name)


def _load(name: str) -> ModuleType:
    """Import one llama_index module; a missing extra is a typed error."""
    try:
        return _import_module(name)
    except ModuleNotFoundError as exc:
        raise LlamaIndexDependencyError(
            "The LlamaIndex backend needs the llama-index packages (the [llamaindex] extra)."
        ) from exc


async def create_initialized_rag_backend(cfg: dict) -> LlamaIndexPostgresBackend | None:
    """Build and initialize the backend, or answer None without the extra.

    The server wiring calls this ONE factory seam. A base install (no
    ``[llamaindex]`` extra) logs one honest line and answers ``None`` — the
    server boots with the RAG mirror off and never crashes (the notebooks
    pattern). Direct construction without the extra raises the typed
    :class:`LlamaIndexDependencyError` with the install command.
    """
    try:
        backend = LlamaIndexPostgresBackend(cfg)
        await backend.initialize()
    except DependencyMissingError:
        logger.warning(
            "The [llamaindex] extra is not installed — the LlamaIndex RAG mirror stays off. "
            "Install it with: pip install 'corpus-kb[llamaindex]'."
        )
        return None
    return backend


class LlamaIndexPostgresBackend:
    """Async RAG backend backed by LlamaIndex, PGVectorStore, and Ollama."""

    def __init__(self, config: dict) -> None:
        """Load connection and embedding settings from config."""
        db_cfg = config.get("database", {})
        emb_cfg = config.get("embedding", {})

        self._connection_string = str(db_cfg.get("connection_string", "")) or str(
            config.get("database_connection_string", "")
        )
        self._embed_model = str(emb_cfg.get("model", "qwen3-embedding:4b"))
        self._embed_dimensions = int(emb_cfg.get("dimensions", NOMIC_EMBEDDING_DIMENSIONS))
        self._embed_base_url = str(emb_cfg.get("base_url", "http://localhost:11434"))

        ollama_embeddings = _load("llama_index.embeddings.ollama")
        self._vector_store: _VectorStoreClient | None = None
        self._ollama_embedding: _EmbeddingClient = ollama_embeddings.OllamaEmbedding(
            model_name=self._embed_model,
            base_url=self._embed_base_url,
            embed_batch_size=1,
        )

    async def initialize(self) -> None:
        """Create the PGVectorStore and verify Ollama is reachable."""
        if not self._connection_string:
            raise InternalError("LlamaIndexPostgresBackend requires database.connection_string")

        # Probe the embedding endpoint, but do not refuse to boot: the
        # embedder's zero-vector fallback keeps the server usable, and the
        # doctor reports the degraded state. Boot must survive a model
        # that is not pulled yet (CI pulls a different default model).
        # The probe is bounded at 3 s; a slow or hung endpoint degrades.
        try:
            await asyncio.wait_for(
                self._ollama_embedding.aget_text_embedding("ping"), LLAMAINDEX_PROBE_TIMEOUT_S
            )
        except Exception as exc:
            logger.warning(
                "Ollama embedding check failed (%s); continuing in degraded zero-vector mode",
                exc,
            )

        pgvector_stores = _load("llama_index.vector_stores.postgres")
        vector_store = pgvector_stores.PGVectorStore.from_params(
            connection_string=self._connection_string,
            async_connection_string=self._connection_string,
            table_name="corpus_rag_nodes",
            schema_name="corpus_rag",
            embed_dim=self._embed_dimensions,
            hnsw_kwargs={
                "hnsw_m": 16,
                "hnsw_ef_construction": 64,
                "hnsw_ef_search": 40,
                "hnsw_dist_method": "vector_cosine_ops",
            },
        )

        if vector_store.embed_dim != self._embed_dimensions:
            raise DimensionMismatchError(
                f"Configured embedding dimensions ({self._embed_dimensions}) "
                f"do not match vector store dimensions ({vector_store.embed_dim})"
            )
        self._vector_store = vector_store

        logger.info("LlamaIndexPostgresBackend initialized")

    async def ingest(self, source_id: str, chunks: list[dict]) -> None:
        """Index chunks as LlamaIndex TextNodes in PGVectorStore."""
        if self._vector_store is None:
            raise InternalError("Backend not initialized. Call initialize() first.")

        core = _load("llama_index.core")
        core_schema = _load("llama_index.core.schema")
        nodes: list[object] = []
        for index, chunk in enumerate(chunks):
            node = core_schema.TextNode(
                id_=f"{source_id}_{index}",
                text=str(chunk.get("text", "")),
                metadata={
                    "source_id": source_id,
                    "chunk_index": index,
                    **chunk.get("metadata", {}),
                },
            )
            node.relationships[core_schema.NodeRelationship.SOURCE] = core_schema.RelatedNodeInfo(
                node_id=source_id
            )
            nodes.append(node)

        if not nodes:
            return

        index_obj = core.VectorStoreIndex.from_vector_store(
            vector_store=self._vector_store,
            embed_model=self._ollama_embedding,
        )
        insert_nodes = getattr(index_obj, "async_insert_nodes", None)
        if insert_nodes is None:
            return
        await insert_nodes(nodes)
        logger.info("Indexed %d LlamaIndex nodes for source %s", len(nodes), source_id)

    async def retrieve(
        self,
        query: str,
        top_k: int,
        filters: dict | None = None,
    ) -> list:
        """Retrieve top_k relevant nodes for the query."""
        if self._vector_store is None:
            raise InternalError("Backend not initialized. Call initialize() first.")

        core = _load("llama_index.core")
        index_obj = core.VectorStoreIndex.from_vector_store(
            vector_store=self._vector_store,
            embed_model=self._ollama_embedding,
        )
        retriever = index_obj.as_retriever(similarity_top_k=top_k)
        nodes = await retriever.aretrieve(query)

        results = []
        for node_with_score in nodes:
            node = node_with_score.node
            results.append(
                {
                    "node_id": node.id_,
                    "source_id": node.metadata.get("source_id", ""),
                    "text": str(getattr(node, "text", "")),
                    "score": float(node_with_score.score or 0.0),
                    "metadata": dict(node.metadata),
                }
            )
        return results

    async def delete_source(self, source_id: str) -> None:
        """Delete all nodes referencing the given source id."""
        if self._vector_store is None:
            raise InternalError("Backend not initialized. Call initialize() first.")

        await self._vector_store.adelete(ref_doc_id=source_id)
        logger.info("Deleted LlamaIndex nodes for source %s", source_id)

    async def health(self) -> bool:
        """Return True if the vector store is initialized."""
        return self._vector_store is not None
