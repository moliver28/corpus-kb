"""Shared helpers for the thin ingest orchestrator."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional, cast

from src.config import load_config
from src.extraction import create_extractor
from src.ontology import Ontology, load_ontology
from src.partitioning import ElementProxy, partition as unstructured_partition
from src.chunking.unstructured_chunker import chunk_elements
from src.rag import OllamaEmbedder
from src.storage.graph_store import GraphStore, create_graph_store
from src.storage.lance_store import LanceDBStore
from src.utils.models import Chunk, Document, Entity, Relation


def load_config_or_pass(config: Optional[dict[str, object]]) -> dict[str, object]:
    """Return the provided config dict, or load the default config if None.

    Args:
        config: A config dict, or None to load the default.

    Returns:
        The config dict to use for pipeline operations.
    """
    return config if config is not None else load_config()


def _nested_dict(config: dict[str, object], key: str) -> dict[str, object]:
    value = config.get(key)
    if isinstance(value, dict):
        return cast(dict[str, object], value)
    return {}


def graph_store_from_config(config: dict[str, object]) -> GraphStore:
    """Create a GraphStore from config keys ``storage.graph_db`` and ``graph.backend``.

    Args:
        config: Pipeline config dict.

    Returns:
        A GraphStore instance (SQLite by default).
    """
    storage = _nested_dict(config, "storage")
    graph_db = "./data/graph.db"
    db = storage.get("graph_db")
    if isinstance(db, str):
        graph_db = db
    graph = _nested_dict(config, "graph")
    backend = graph.get("backend")
    if not isinstance(backend, str):
        backend = "sqlite"
    return create_graph_store(backend, graph_db)


def lance_store_from_config(config: dict[str, object]) -> Optional[LanceDBStore]:
    """Create a LanceDBStore from config keys ``storage.lancedb_uri`` and ``embedding.dimensions``.

    Args:
        config: Pipeline config dict.

    Returns:
        A LanceDBStore instance, or None if ``lancedb_uri`` is not set.
    """
    storage = _nested_dict(config, "storage")
    uri = storage.get("lancedb_uri")
    if not isinstance(uri, str):
        return None
    embedding = _nested_dict(config, "embedding")
    dimensions = embedding.get("dimensions")
    if not isinstance(dimensions, int):
        dimensions = 768
    return LanceDBStore(uri, dimensions)


def ontology(config: dict[str, object]) -> Ontology:
    """Load the ontology from ``graph.ontology_path`` in config, falling back to default.

    Args:
        config: Pipeline config dict with optional ``graph.ontology_path`` key.

    Returns:
        The loaded Ontology instance.
    """
    graph = _nested_dict(config, "graph")
    ontology_path = graph.get("ontology_path")
    if not isinstance(ontology_path, str):
        ontology_path = "config/ontology.yaml"
    return load_ontology(ontology_path)


def elements_for_text(text: str) -> list[ElementProxy]:
    """Wrap raw text into a single-element list for the chunking pipeline.

    Args:
        text: Raw text content.

    Returns:
        A list with one ElementProxy wrapping the text.
    """
    return [
        ElementProxy(text=text, element_type="NarrativeText", element_id="raw-text")
    ]


def elements_for_file(path: Path) -> list[ElementProxy]:
    """Partition a file into unstructured elements for chunking.

    Args:
        path: Path to the file to partition.

    Returns:
        A list of ElementProxy objects from the unstructured partitioner.
    """
    return unstructured_partition(path)


def build_document(path: str, source_type: str, text: str) -> Document:
    """Construct a Document model from raw text.

    Args:
        path: Source path or "raw_text".
        source_type: One of "code", "markdown", "text".
        text: The full text content.

    Returns:
        A Document instance with size_bytes computed from the text.
    """
    return Document(
        path=path,
        source_type=source_type,
        content=text,
        size_bytes=len(text.encode("utf-8")),
    )


def persist_chunks(graph_store: GraphStore, chunks: list[Chunk]) -> None:
    """Persist all chunks to the graph store's provenance table.

    Args:
        graph_store: The GraphStore to write to.
        chunks: List of Chunk objects to persist.
    """
    for chunk in chunks:
        graph_store.add_chunk(chunk)


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


def store_vectors(
    chunks: list[Chunk], lance_store: Optional[LanceDBStore]
) -> tuple[bool, str | None]:
    """Persist chunk vectors to LanceDB, returning (degraded, error_message).

    If ``lance_store`` is None, returns ``(True, "LanceDBStore not configured")``
    so the caller knows vectors were not stored. On failure, returns
    ``(True, "ExceptionType: message")``.
    """
    if lance_store is None:
        return True, "LanceDBStore not configured"
    try:
        lance_store.add_chunks(chunks)
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
    """Extract entities and relations, falling back to RegexExtractor on failure.

    Args:
        chunks: List of Chunk objects to extract from.
        ontology: The Ontology constraining entity/relation types.
        source_document_id: The document ID for provenance.
        config: Pipeline config dict with ``graph.extractor`` key.

    Returns:
        Tuple of (entities, relations, extractor_id).
    """
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


def run_pipeline(
    text: str,
    source_type: str,
    path: str,
    config: dict[str, object],
    graph_store: GraphStore,
) -> dict[str, object]:
    """Run the full ingest pipeline: partition, chunk, embed, extract, store.

    Graph writes (document, chunks, entities, relations) are wrapped in a
    single SQLite transaction for atomicity. Vector writes to LanceDB happen
    outside the transaction. On extraction failure, all graph writes roll back.

    Args:
        text: The full text content to ingest.
        source_type: One of "code", "markdown", "text".
        path: Source path or "raw_text".
        config: Pipeline config dict.
        graph_store: The GraphStore to write provenance and graph data to.

    Returns:
        Result dict with keys: status, document_id, path, source_type,
        size_bytes, chunk_count, entity_count, relation_count,
        lance_row_count, degraded, extractor_id, entities, errors.
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

    lance_store = lance_store_from_config(config)
    store_degraded, store_err = store_vectors(chunks, lance_store)
    if store_err is not None:
        logging.warning("store_vectors: %s", store_err)
        errors.append(f"VectorStoreError: {store_err}")
    degraded = store_degraded or degraded

    entities: list[Entity] = []
    relations: list[Relation] = []
    extractor_id = "none"
    with graph_store.transaction():
        graph_store.add_document(document)
        persist_chunks(graph_store, chunks)

        if _extract_entities_flag(config):
            entities, relations, extractor_id = extract_with_fallback(
                chunks, ontology(config), document.document_id, config
            )
            if entities:
                graph_store.batch_add_entities(entities)
            if relations:
                graph_store.batch_add_relations(relations)

        document.chunk_count = len(chunks)
        graph_store.add_document(document)

    lance_count = 0
    if lance_store is not None:
        try:
            lance_count = lance_store.count_rows()
        except Exception:
            pass

    return {
        "status": "success",
        "document_id": document.document_id,
        "path": path,
        "source_type": source_type,
        "size_bytes": document.size_bytes,
        "chunk_count": len(chunks),
        "entity_count": len(entities),
        "relation_count": len(relations),
        "lance_row_count": lance_count,
        "degraded": degraded,
        "extractor_id": extractor_id,
        "entities": {entity.name: entity.entity_id for entity in entities},
        "errors": errors,
    }
