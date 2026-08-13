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

from ..config import load_config
from ..extraction import create_extractor
from ..ontology import Ontology, load_ontology
from ..partitioning import ElementProxy, partition as unstructured_partition
from ..chunking.unstructured_chunker import chunk_elements
from ..rag import OllamaEmbedder
from ..storage.rag_backend import RagBackend
from ..storage.tenant_conn import tenant_connection
from ..utils.models import Chunk, Document, Entity, Relation

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


CAPTION_SUFFIXES = {".vtt", ".srt"}


def read_caption_text(path: Path) -> str:
    """Flatten a WebVTT/SRT caption file to plain transcript text.

    Unstructured does not recognize caption formats, but interview transcripts
    are commonly exported as .vtt/.srt. Strip the header, cue numbers, and
    timestamp lines, keeping only the spoken text.
    """
    lines: list[str] = []
    for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw.strip()
        if not line:
            continue
        if line == "WEBVTT" or line.startswith("NOTE"):
            continue
        if "-->" in line:  # timestamp cue
            continue
        if line.isdigit():  # SRT cue number
            continue
        lines.append(line)
    return "\n".join(lines)


def read_pdf_text(path: Path) -> str:
    """Extract text from a PDF with pypdf (already a dependency).

    Unstructured's PDF path pulls a heavy image/OCR stack just to import, which
    is overkill for the text-based reports UX research produces. pypdf handles
    those directly. Scanned/image-only PDFs yield little text and would need a
    separate OCR path.
    """
    from pypdf import PdfReader

    reader = PdfReader(str(path))
    return "\n\n".join((page.extract_text() or "").strip() for page in reader.pages)


def elements_for_file(path: Path) -> list[ElementProxy]:
    """Partition a file into unstructured elements for chunking.

    Caption transcripts (.vtt/.srt) and PDFs are handled with lightweight
    dedicated readers; everything else goes through Unstructured.
    """
    suffix = path.suffix.lower()
    if suffix in CAPTION_SUFFIXES:
        return elements_for_text(read_caption_text(path))
    if suffix == ".pdf":
        return elements_for_text(read_pdf_text(path))
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
        if vectors and all(all(v == 0.0 for v in vector) for vector in vectors):
            return True, "Connection failed: OllamaEmbedder returned zero vectors"
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

    from ..extraction import RegexExtractor

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
        async with tenant_connection(self._pool, self._tenant_id) as conn:
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

    async def store_chunks(self, chunks: list[Chunk]) -> set[str]:
        """Insert chunks into the chunks table. Returns the chunk_ids actually inserted.

        A chunk whose (tenant_id, doc_id, chunk_index) already exists is skipped
        (ON CONFLICT DO NOTHING) -- its chunk_id is a fresh UUID generated for
        this call and was never written, so callers must not treat it as
        present (store_vectors would hit a foreign-key violation otherwise).
        """
        if not chunks:
            return set()
        inserted: set[str] = set()
        async with tenant_connection(self._pool, self._tenant_id) as conn:
            for count, chunk in enumerate(chunks):
                chunk_index = (
                    chunk.sibling_order if chunk.sibling_order is not None else count
                )
                row = await conn.fetchrow(
                    """
                    INSERT INTO chunks (chunk_id, tenant_id, doc_id, chunk_index,
                        text, source_type, entity_name, heading_path, file_path,
                        start_line, end_line, metadata)
                    VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12)
                    ON CONFLICT (tenant_id, doc_id, chunk_index) DO NOTHING
                    RETURNING chunk_id
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
                if row is not None:
                    inserted.add(str(row["chunk_id"]))
        return inserted

    async def store_vectors(self, chunks: list[Chunk], embedding_model: str) -> int:
        """Insert chunk vectors into the chunks_vectors table. Returns count."""
        if not chunks:
            return 0
        count = 0
        async with tenant_connection(self._pool, self._tenant_id) as conn:
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
                    embedding_model,
                )
                count += 1
        return count

    async def store_entities(self, entities: list[Entity]) -> dict[str, str]:
        """Insert entities into the entities table.

        Returns a map of each input entity's original entity_id to the
        entity_id actually persisted for it. An entity whose (tenant_id,
        name, entity_type) already exists is skipped (ON CONFLICT DO
        NOTHING) -- its real, pre-existing entity_id differs from the
        fresh one generated for this call, so callers building relations
        must remap through this dict rather than assume the original id.
        """
        if not entities:
            return {}
        resolved: dict[str, str] = {}
        async with tenant_connection(self._pool, self._tenant_id) as conn:
            for entity in entities:
                row = await conn.fetchrow(
                    """
                    INSERT INTO entities (entity_id, tenant_id, name, entity_type,
                        source_document_id, metadata)
                    VALUES ($1, $2, $3, $4, $5, $6)
                    ON CONFLICT (tenant_id, name, entity_type) DO NOTHING
                    RETURNING entity_id
                    """,
                    entity.entity_id,
                    self._tenant_id,
                    entity.name,
                    entity.entity_type,
                    entity.source_document_id,
                    json.dumps(entity.metadata),
                )
                if row is not None:
                    resolved[str(entity.entity_id)] = str(row["entity_id"])
                else:
                    existing = await conn.fetchrow(
                        """
                        SELECT entity_id FROM entities
                        WHERE tenant_id = $1 AND name = $2 AND entity_type = $3
                        """,
                        self._tenant_id,
                        entity.name,
                        entity.entity_type,
                    )
                    if existing is not None:
                        resolved[str(entity.entity_id)] = str(existing["entity_id"])
        return resolved

    async def store_relations(self, relations: list[Relation]) -> int:
        """Insert relations into the relations table. Returns count."""
        if not relations:
            return 0
        count = 0
        async with tenant_connection(self._pool, self._tenant_id) as conn:
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
    rag_backend: RagBackend | None = None,
    is_file: bool = False,
) -> dict[str, object]:
    """Run the full ingest pipeline: partition, chunk, embed, extract, store.

    All writes go directly to Postgres via asyncpg. No LanceDB, no SQLite.

    Args:
        text: The full text content to ingest.
        source_type: One of "code", "markdown", "text".
        path: Source path or a custom source identifier.
        config: Pipeline config dict.
        pg_pool: asyncpg connection pool for Postgres writes.
        tenant_id: Tenant ID for RLS.
        is_file: True if path points to a file; False if path is raw text source ID.

    Returns:
        Result dict with keys: status, document_id, path, source_type,
        size_bytes, chunk_count, entity_count, relation_count,
        pg_chunk_count, pg_vector_count, degraded, extractor_id, entities, errors.
    """
    if is_file:
        # Let Unstructured parse the file (PDF, DOCX, HTML, etc.) and derive the
        # document text from the extracted elements. Do NOT read the file as
        # UTF-8 first: binary formats would raise UnicodeDecodeError before ever
        # reaching the partitioner.
        elements = elements_for_file(Path(path))
        text = "\n\n".join(e.text for e in elements if e.text)
    else:
        elements = elements_for_text(text)

    document = build_document(path, source_type, text)
    chunks = chunk_elements(elements, text, document.document_id)

    errors: list[str] = []
    degraded, embed_err = embed_chunks(chunks, config)
    if embed_err is not None:
        logging.warning("embed_chunks: %s", embed_err)
        errors.append(f"EmbeddingError: {embed_err}")

    ingest_store = PostgresIngestStore(pg_pool, tenant_id)

    # Write document + chunks + vectors to Postgres. Each store call commits
    # its own transaction independently, so a later call's failure must not
    # zero out an earlier call's already-committed count.
    pg_chunk_count = 0
    pg_vector_count = 0
    try:
        document_id = await ingest_store.store_document(document)
        document.document_id = document_id
        # Update chunk references to use the actual stored document ID
        for chunk in chunks:
            chunk.document_id = document_id
    except Exception as exc:
        logging.warning("Postgres document write failed: %s", exc)
        errors.append(f"PostgresWriteError: {exc}")

    inserted_chunk_ids: set[str] = set()
    try:
        inserted_chunk_ids = await ingest_store.store_chunks(chunks)
        pg_chunk_count = len(inserted_chunk_ids)
    except Exception as exc:
        logging.warning("Postgres chunk write failed: %s", exc)
        errors.append(f"PostgresWriteError: {exc}")

    # Only chunks that were actually inserted this call have a row in
    # `chunks` to satisfy chunks_vectors' foreign key -- a chunk skipped by
    # store_chunks' ON CONFLICT DO NOTHING (an unchanged re-ingest) keeps its
    # freshly-generated chunk_id un-persisted, so embedding it would fail.
    new_chunks = [c for c in chunks if str(c.chunk_id) in inserted_chunk_ids]
    try:
        embedding_model = str(_nested_dict(config, "embedding").get("model", "nomic-embed-text"))
        pg_vector_count = await ingest_store.store_vectors(new_chunks, embedding_model)
    except Exception as exc:
        logging.warning("Postgres vector write failed: %s", exc)
        errors.append(f"PostgresWriteError: {exc}")

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
                resolved_ids = await ingest_store.store_entities(entities)
                for entity in entities:
                    resolved = resolved_ids.get(str(entity.entity_id))
                    if resolved is not None:
                        entity.entity_id = resolved
                for relation in relations:
                    relation.source_entity_id = resolved_ids.get(
                        str(relation.source_entity_id), relation.source_entity_id
                    )
                    relation.target_entity_id = resolved_ids.get(
                        str(relation.target_entity_id), relation.target_entity_id
                    )
            if relations:
                await ingest_store.store_relations(relations)
        except Exception as exc:
            logging.warning("Entity extraction failed: %s", exc)
            errors.append(f"ExtractionError: {exc}")

    # Mirror chunks into LlamaIndex RAG backend (additive path)
    if rag_backend is not None:
        try:
            await rag_backend.ingest(
                document.document_id,
                [c.model_dump() for c in chunks],
            )
        except Exception as exc:
            logging.warning("LlamaIndex ingest failed: %s", exc)
            errors.append(f"LlamaIndexError: {exc}")

    # Update document chunk_count
    document.chunk_count = len(chunks)
    try:
        await ingest_store.store_document(document)
    except Exception as exc:
        logging.warning("Document update failed: %s", exc)
        errors.append(f"PostgresWriteError: {exc}")

    # A dead Ollama (EmbeddingError) is an already-handled, non-fatal
    # degradation -- surfaced via `degraded` and kept in `errors` for
    # visibility, but it must not flip status to "error" on its own.
    # Anything else here (a Postgres write failing, extraction blowing up)
    # means data genuinely didn't land, so it does.
    hard_errors = [e for e in errors if not e.startswith("EmbeddingError:")]

    return {
        "status": "error" if hard_errors else "success",
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
