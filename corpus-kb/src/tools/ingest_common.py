"""Shared helpers for the thin ingest orchestrator.

All writes go directly to Postgres via asyncpg. No LanceDB, no SQLite.
The ingest pipeline is async — callers must await run_pipeline().
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Optional

import asyncpg

from src.config import load_config
from src.extraction import create_extractor
from src.ontology import Ontology, load_ontology
from src.partitioning import ElementProxy, partition as unstructured_partition
from src.chunking.unstructured_chunker import chunk_elements
from src.rag import OllamaEmbedder
from src.utils.models import Chunk, Document, Entity, Relation

logger = logging.getLogger(__name__)

DEFAULT_TENANT_ID = "00000000-0000-0000-0000-000000000001"


def load_config_or_pass(config: Optional[dict[str, object]]) -> dict[str, object]:
    """Return the provided config dict, or load the default config if None."""
    return config if config is not None else load_config()


def _nested_dict(config: dict[str, object], key: str) -> dict[str, object]:
    value = config.get(key)
    if isinstance(value, dict):
        return value
    return {}


def ontology(config: dict[str, object]) -> Ontology:
    """Load the ontology from ``graph.ontology_path`` in config, falling back to default."""
    graph = _nested_dict(config, "graph")
    ontology_path = graph.get("ontology_path")
    if not isinstance(ontology_path, str):
        ontology_path = "config/ontology.yaml"
    return load_ontology(ontology_path)


def elements_for_text(text: str) -> list[ElementProxy]:
    """Wrap raw text into a single-element list for the chunking pipeline."""
    return [
        ElementProxy(text=text, element_type="NarrativeText", element_id="raw-text")
    ]


def elements_for_file(path: Path) -> list[ElementProxy]:
    """Partition a file into unstructured elements for chunking."""
    return unstructured_partition(path)


def build_document(path: str, source_type: str, text: str) -> Document:
    """Construct a Document model from raw text."""
    return Document(
        path=path,
        source_type=source_type,
        content=text,
        size_bytes=len(text.encode("utf-8")),
    )


def embed_chunks(
    chunks: list[Chunk], config: dict[str, object]
) -> tuple[bool, str | None]:
    """Embed chunk texts via Ollama, returning (degraded, error_message).

    On success, each chunk's ``embedding`` field is populated. On failure,
    returns ``(True, "ExceptionType: message")`` so the caller can report
    structured error info.
    """
    try:
        embedder = OllamaEmbedder(config)
        texts = [chunk.text for chunk in chunks]
        vectors = embedder.embed_batch(texts)
        for chunk, vector in zip(chunks, vectors, strict=True):
            chunk.embedding = vector
        return False, None
    except Exception as exc:
        return True, f"{type(exc).__name__}: {exc}"


def _extractor_name(config: dict[str, object]) -> str:
    graph = _nested_dict(config, "graph")
    raw = graph.get("extractor")
    return raw if isinstance(raw, str) else "regex"


def extract_with_fallback(
    chunks: list[Chunk],
    ontology: Ontology,
    source_document_id: str,
    config: dict[str, object],
) -> tuple[list[Entity], list[Relation], str]:
    """Extract entities and relations, falling back to RegexExtractor on failure."""
    extractor_name = _extractor_name(config)

    if extractor_name == "langextract":
        extractor = create_extractor(config)
        try:
            entities, relations = extractor.extract(
                chunks, ontology, source_document_id
            )
            if entities:
                return entities, relations, extractor.extractor_id
        except (FileNotFoundError, ImportError, ModuleNotFoundError):
            pass

    from src.extraction import RegexExtractor

    fallback = RegexExtractor()
    entities, relations = fallback.extract(chunks, ontology, source_document_id)
    return entities, relations, fallback.extractor_id


def _extract_entities_flag(config: dict[str, object]) -> bool:
    graph = _nested_dict(config, "graph")
    flag = graph.get("extract_entities")
    return flag if isinstance(flag, bool) else True


class PostgresIngestStore:
    """Wraps all Postgres write operations for the ingest pipeline.

    All methods use the same asyncpg.Pool and set tenant context via SET LOCAL.
    """

    def __init__(
        self,
        pool: asyncpg.Pool,
        tenant_id: str = DEFAULT_TENANT_ID,
    ) -> None:
        self._pool = pool
        self._tenant_id = tenant_id

    async def store_document(self, document: Document) -> str:
        """Insert a document into the documents table."""
        async with self._pool.acquire() as conn:
            await conn.execute(
                "SELECT set_config('app.current_tenant_id', $1, true)",
                self._tenant_id,
            )
            row = await conn.fetchrow(
                """
                INSERT INTO documents (doc_id, tenant_id, source, source_type,
                    chunk_count, file_size, metadata)
                VALUES ($1, $2, $3, $4, $5, $6, $7)
                ON CONFLICT (tenant_id, source) DO UPDATE
                SET chunk_count = $5, file_size = $6, updated_at = NOW()
                RETURNING doc_id::text
                """,
                document.document_id,
                self._tenant_id,
                document.path,
                document.source_type,
                document.chunk_count,
                document.size_bytes,
                json.dumps(document.metadata),
            )
            return str(row["doc_id"])

    async def store_chunks(self, chunks: list[Chunk]) -> int:
        """Insert chunks into the chunks table. Returns count inserted."""
        if not chunks:
            return 0
        count = 0
        async with self._pool.acquire() as conn:
            await conn.execute(
                "SELECT set_config('app.current_tenant_id', $1, true)",
                self._tenant_id,
            )
            for chunk in chunks:
                chunk_index = (
                    chunk.sibling_order if chunk.sibling_order is not None else count
                )
                await conn.execute(
                    """
                    INSERT INTO chunks (chunk_id, tenant_id, doc_id, chunk_index,
                        text, source_type, entity_name, heading_path, file_path,
                        start_line, end_line, metadata)
                    VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12)
                    ON CONFLICT (tenant_id, doc_id, chunk_index) DO NOTHING
                    """,
                    chunk.chunk_id,
                    self._tenant_id,
                    chunk.document_id,
                    chunk_index,
                    chunk.text,
                    chunk.source_type,
                    chunk.entity_name,
                    json.dumps(chunk.heading_path) if chunk.heading_path else None,
                    chunk.metadata.get("file_path") if chunk.metadata else None,
                    chunk.start_line,
                    chunk.end_line,
                    json.dumps(chunk.metadata),
                )
                count += 1
        return count

    async def store_vectors(self, chunks: list[Chunk]) -> int:
        """Insert chunk vectors into the chunks_vectors table. Returns count."""
        if not chunks:
            return 0
        count = 0
        async with self._pool.acquire() as conn:
            await conn.execute(
                "SELECT set_config('app.current_tenant_id', $1, true)",
                self._tenant_id,
            )
            for chunk in chunks:
                if chunk.embedding is None:
                    continue
                vector_str = "[" + ",".join(str(v) for v in chunk.embedding) + "]"
                await conn.execute(
                    """
                    INSERT INTO chunks_vectors (chunk_id, tenant_id, vector, embedding_model)
                    VALUES ($1, $2, $3::vector, $4)
                    ON CONFLICT (chunk_id) DO UPDATE
                    SET vector = $3::vector, embedding_model = $4, embedded_at = NOW()
                    """,
                    chunk.chunk_id,
                    self._tenant_id,
                    vector_str,
                    "nomic-embed-text",
                )
                count += 1
        return count

    async def store_entities(self, entities: list[Entity]) -> int:
        """Insert entities into the entities table. Returns count."""
        if not entities:
            return 0
        count = 0
        async with self._pool.acquire() as conn:
            await conn.execute(
                "SELECT set_config('app.current_tenant_id', $1, true)",
                self._tenant_id,
            )
            for entity in entities:
                await conn.execute(
                    """
                    INSERT INTO entities (entity_id, tenant_id, name, entity_type,
                        source_document_id, metadata)
                    VALUES ($1, $2, $3, $4, $5, $6)
                    ON CONFLICT (tenant_id, name, entity_type) DO NOTHING
                    """,
                    entity.entity_id,
                    self._tenant_id,
                    entity.name,
                    entity.entity_type,
                    entity.source_document_id,
                    json.dumps(entity.metadata),
                )
                count += 1
        return count

    async def store_relations(self, relations: list[Relation]) -> int:
        """Insert relations into the relations table. Returns count."""
        if not relations:
            return 0
        count = 0
        async with self._pool.acquire() as conn:
            await conn.execute(
                "SELECT set_config('app.current_tenant_id', $1, true)",
                self._tenant_id,
            )
            for relation in relations:
                await conn.execute(
                    """
                    INSERT INTO relations (relation_id, tenant_id, source_entity_id,
                        target_entity_id, relation_type, weight, metadata)
                    VALUES ($1, $2, $3, $4, $5, $6, $7)
                    ON CONFLICT (tenant_id, source_entity_id, target_entity_id, relation_type)
                    DO NOTHING
                    """,
                    relation.relation_id,
                    self._tenant_id,
                    relation.source_entity_id,
                    relation.target_entity_id,
                    relation.relation_type,
                    relation.weight if relation.weight else 1.0,
                    json.dumps(relation.metadata),
                )
                count += 1
        return count


async def run_pipeline(
    text: str,
    source_type: str,
    path: str,
    config: dict[str, object],
    pg_pool: asyncpg.Pool,
    tenant_id: str = DEFAULT_TENANT_ID,
) -> dict[str, object]:
    """Run the full ingest pipeline: partition, chunk, embed, extract, store.

    All writes go directly to Postgres via asyncpg. No LanceDB, no SQLite.

    Args:
        text: The full text content to ingest.
        source_type: One of "code", "markdown", "text".
        path: Source path or "raw_text".
        config: Pipeline config dict.
        pg_pool: asyncpg connection pool for Postgres writes.
        tenant_id: Tenant ID for RLS.

    Returns:
        Result dict with keys: status, document_id, path, source_type,
        size_bytes, chunk_count, entity_count, relation_count,
        pg_chunk_count, pg_vector_count, degraded, extractor_id, entities, errors.
    """
    document = build_document(path, source_type, text)

    if path == "raw_text":
        elements = elements_for_text(text)
    else:
        elements = elements_for_file(Path(path))

    chunks = chunk_elements(elements, text, document.document_id)

    errors: list[str] = []
    degraded, embed_err = embed_chunks(chunks, config)
    if embed_err is not None:
        logging.warning("embed_chunks: %s", embed_err)
        errors.append(f"EmbeddingError: {embed_err}")

    ingest_store = PostgresIngestStore(pg_pool, tenant_id)

    # Write document + chunks + vectors to Postgres
    try:
        await ingest_store.store_document(document)
        pg_chunk_count = await ingest_store.store_chunks(chunks)
        pg_vector_count = await ingest_store.store_vectors(chunks)
    except Exception as exc:
        logging.warning("Postgres write failed: %s", exc)
        errors.append(f"PostgresWriteError: {exc}")
        pg_chunk_count = 0
        pg_vector_count = 0

    # Extract entities and relations
    entities: list[Entity] = []
    relations: list[Relation] = []
    extractor_id = "none"
    if _extract_entities_flag(config):
        try:
            entities, relations, extractor_id = extract_with_fallback(
                chunks, ontology(config), document.document_id, config
            )
            if entities:
                await ingest_store.store_entities(entities)
            if relations:
                await ingest_store.store_relations(relations)
        except Exception as exc:
            logging.warning("Entity extraction failed: %s", exc)
            errors.append(f"ExtractionError: {exc}")

    # Update document chunk_count
    document.chunk_count = len(chunks)
    try:
        await ingest_store.store_document(document)
    except Exception as exc:
        logging.warning("Document update failed: %s", exc)

    return {
        "status": "success",
        "document_id": document.document_id,
        "path": path,
        "source_type": source_type,
        "size_bytes": document.size_bytes,
        "chunk_count": len(chunks),
        "entity_count": len(entities),
        "relation_count": len(relations),
        "pg_chunk_count": pg_chunk_count,
        "pg_vector_count": pg_vector_count,
        "degraded": degraded,
        "extractor_id": extractor_id,
        "entities": {entity.name: entity.entity_id for entity in entities},
        "errors": errors,
    }
