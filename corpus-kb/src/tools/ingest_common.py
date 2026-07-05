"""Shared helpers for the thin ingest orchestrator."""

from __future__ import annotations

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
    return config if config is not None else load_config()


def _nested_dict(config: dict[str, object], key: str) -> dict[str, object]:
    value = config.get(key)
    if isinstance(value, dict):
        return cast(dict[str, object], value)
    return {}


def graph_store_from_config(config: dict[str, object]) -> GraphStore:
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
    storage = _nested_dict(config, "storage")
    uri = storage.get("lancedb_uri")
    if not isinstance(uri, str):
        return None
    embedding = _nested_dict(config, "embedding")
    dimensions = embedding.get("dimensions")
    if not isinstance(dimensions, int):
        dimensions = 768
    return LanceDBStore(uri, dimensions)


def ontology() -> Ontology:
    return load_ontology("config/ontology.yaml")


def elements_for_text(text: str) -> list[ElementProxy]:
    return [
        ElementProxy(text=text, element_type="NarrativeText", element_id="raw-text")
    ]


def elements_for_file(path: Path) -> list[ElementProxy]:
    return unstructured_partition(path)


def build_document(path: str, source_type: str, text: str) -> Document:
    return Document(
        path=path,
        source_type=source_type,
        content=text,
        size_bytes=len(text.encode("utf-8")),
    )


def persist_chunks(graph_store: GraphStore, chunks: list[Chunk]) -> None:
    for chunk in chunks:
        graph_store.add_chunk(chunk)


def embed_chunks(chunks: list[Chunk], config: dict[str, object]) -> bool:
    try:
        embedder = OllamaEmbedder(config)
        texts = [chunk.text for chunk in chunks]
        vectors = embedder.embed_batch(texts)
        for chunk, vector in zip(chunks, vectors, strict=True):
            chunk.embedding = vector
        return False
    except Exception:
        return True


def store_vectors(chunks: list[Chunk], lance_store: Optional[LanceDBStore]) -> bool:
    if lance_store is None:
        return True
    try:
        lance_store.add_chunks(chunks)
        return False
    except Exception:
        return True


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
    document = build_document(path, source_type, text)
    graph_store.add_document(document)

    if path == "raw_text":
        elements = elements_for_text(text)
    else:
        elements = elements_for_file(Path(path))

    chunks = chunk_elements(elements, text, document.document_id)
    persist_chunks(graph_store, chunks)

    degraded = embed_chunks(chunks, config)
    lance_store = lance_store_from_config(config)
    degraded = store_vectors(chunks, lance_store) or degraded

    entities: list[Entity] = []
    relations: list[Relation] = []
    extractor_id = "none"
    if _extract_entities_flag(config):
        entities, relations, extractor_id = extract_with_fallback(
            chunks, ontology(), document.document_id, config
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
    }
