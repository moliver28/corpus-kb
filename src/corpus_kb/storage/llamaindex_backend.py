"""LlamaIndex-backed RAG storage using PGVectorStore and Ollama.

This backend is intentionally isolated from the existing PostgresGraphStore
path: it uses PGVectorStore for vector search and Ollama for local embeddings.
It never falls back to OpenAI/Anthropic/cloud providers.
"""

from __future__ import annotations

import logging

from llama_index.core import VectorStoreIndex
from llama_index.core.schema import NodeRelationship, RelatedNodeInfo, TextNode
from llama_index.embeddings.ollama import OllamaEmbedding
from llama_index.vector_stores.postgres import PGVectorStore

logger = logging.getLogger(__name__)


class DimensionMismatchError(ValueError):
    """Raised when configured embedding dimensions do not match the vector store."""


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
        self._embed_dimensions = int(emb_cfg.get("dimensions", 768))
        self._embed_base_url = str(emb_cfg.get("base_url", "http://localhost:11434"))

        self._vector_store: PGVectorStore | None = None
        self._ollama_embedding = OllamaEmbedding(
            model_name=self._embed_model,
            base_url=self._embed_base_url,
            embed_batch_size=1,
        )

    async def initialize(self) -> None:
        """Create the PGVectorStore and verify Ollama is reachable."""
        if not self._connection_string:
            raise RuntimeError("LlamaIndexPostgresBackend requires database.connection_string")

        # Probe the embedding endpoint, but do not refuse to boot: the
        # embedder's zero-vector fallback keeps the server usable, and the
        # doctor reports the degraded state. Boot must survive a model
        # that is not pulled yet (CI pulls a different default model).
        try:
            await self._ollama_embedding.aget_text_embedding("ping")
        except Exception as exc:
            logger.warning(
                "Ollama embedding check failed (%s); continuing in "
                "degraded zero-vector mode",
                exc,
            )

        vector_store = PGVectorStore.from_params(
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
            raise RuntimeError("Backend not initialized. Call initialize() first.")

        nodes: list[TextNode] = []
        for index, chunk in enumerate(chunks):
            node = TextNode(
                id_=f"{source_id}_{index}",
                text=str(chunk.get("text", "")),
                metadata={
                    "source_id": source_id,
                    "chunk_index": index,
                    **chunk.get("metadata", {}),
                },
            )
            node.relationships[NodeRelationship.SOURCE] = RelatedNodeInfo(node_id=source_id)
            nodes.append(node)

        if not nodes:
            return

        index_obj = VectorStoreIndex.from_vector_store(
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
            raise RuntimeError("Backend not initialized. Call initialize() first.")

        index_obj = VectorStoreIndex.from_vector_store(
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
            raise RuntimeError("Backend not initialized. Call initialize() first.")

        await self._vector_store.adelete(ref_doc_id=source_id)
        logger.info("Deleted LlamaIndex nodes for source %s", source_id)

    async def health(self) -> bool:
        """Return True if the vector store is initialized."""
        return self._vector_store is not None
