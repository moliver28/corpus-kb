"""Shared helpers for the thin ingest orchestrator.

All writes go directly to Postgres via asyncpg. No LanceDB, no SQLite.
The ingest pipeline is async — callers must await run_pipeline().
"""

from __future__ import annotations

import hashlib
import json
import logging
from datetime import datetime, timezone
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


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def load_config_or_pass(config: Optional[dict[str, object]]) -> dict[str, object]:
    """Return the provided config dict, or load the default config if None."""
    return config if config is not None else load_config()


def _nested_dict(config: dict[str, object], key: str) -> dict[str, object]:
    value = config.get(key)
    if isinstance(value, dict):
        return value
    return {}


def _contextual_enabled(config: dict[str, object], source_type: str) -> bool:
    """Return True if contextual retrieval should run for this source_type.

    Contextualization is on unconditionally when ``contextual.enabled`` is
    True. Otherwise it is on only for source_types listed in
    ``contextual.enabled_source_types`` (default-on allowlist for
    interview/research data per the approved feature default).
    """
    cfg = _nested_dict(config, "contextual")
    if bool(cfg.get("enabled", False)):
        return True
    allowlist = cfg.get("enabled_source_types", [])
    return isinstance(allowlist, list) and source_type in allowlist


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
    chunks: list[Chunk], config: dict[str, object], text_override: Optional[dict[str, str]] = None,
) -> tuple[bool, str | None]:
    """Embed chunk texts via Ollama, returning (degraded, error_message).

    On success, each chunk's ``embedding`` field is populated. On failure,
    returns ``(True, "ExceptionType: message")`` so the caller can report
    structured error info. ``text_override``, if provided, is keyed by
    chunk_id (str) and takes precedence over ``chunk.text`` -- used to embed
    the contextualized (blurb + text) form instead of raw chunk text.
    """
    try:
        embedder = OllamaEmbedder(config)
        texts = [
            (text_override or {}).get(str(chunk.chunk_id), chunk.text) for chunk in chunks
        ]
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

    async def store_document(self, document: Document, file_hash: Optional[str] = None) -> tuple[str, bool]:
        """Insert/update a document. Returns (doc_id, unchanged) -- unchanged=True
        short-circuits run_pipeline's chunk/vector/extraction work."""
        async with tenant_connection(self._pool, self._tenant_id) as conn:
            if file_hash is not None:
                existing = await conn.fetchrow(
                    "SELECT doc_id::text, file_hash FROM documents WHERE tenant_id = $1 AND source = $2",
                    self._tenant_id, document.path,
                )
                if existing is not None and existing["file_hash"] == file_hash:
                    return existing["doc_id"], True
            row = await conn.fetchrow(
                """
                INSERT INTO documents (doc_id, tenant_id, source, source_type,
                    chunk_count, file_size, file_hash, metadata)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
                ON CONFLICT (tenant_id, source) DO UPDATE
                SET chunk_count = $5, file_size = $6, file_hash = $7, updated_at = NOW()
                RETURNING doc_id::text
                """,
                document.document_id, self._tenant_id, document.path, document.source_type,
                document.chunk_count, document.size_bytes, file_hash, json.dumps(document.metadata),
            )
            return row["doc_id"], False

    async def store_chunks(self, chunks: list[Chunk]) -> set[str]:
        """Insert/update/tombstone chunks. Returns chunk_ids needing (re-)embedding."""
        if not chunks:
            return set()
        needs_embedding: set[str] = set()
        async with tenant_connection(self._pool, self._tenant_id) as conn:
            for count, chunk in enumerate(chunks):
                chunk_index = chunk.sibling_order if chunk.sibling_order is not None else count
                existing = await conn.fetchrow(
                    "SELECT chunk_id, chunk_hash FROM chunks WHERE tenant_id = $1 AND doc_id = $2 AND chunk_index = $3",
                    self._tenant_id, chunk.document_id, chunk_index,
                )
                if existing is None:
                    row = await conn.fetchrow(
                        """
                        INSERT INTO chunks (chunk_id, tenant_id, doc_id, chunk_index,
                            text, source_type, entity_name, heading_path, file_path,
                            start_line, end_line, metadata, chunk_hash, source_timestamp, context_blurb)
                        VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, $15)
                        ON CONFLICT (tenant_id, doc_id, chunk_index) DO NOTHING
                        RETURNING chunk_id
                        """,
                        chunk.chunk_id, self._tenant_id, chunk.document_id, chunk_index,
                        chunk.text, chunk.source_type, chunk.entity_name,
                        json.dumps(chunk.heading_path) if chunk.heading_path else None,
                        chunk.metadata.get("file_path") if chunk.metadata else None,
                        chunk.start_line, chunk.end_line, json.dumps(chunk.metadata),
                        chunk.chunk_hash, chunk.source_timestamp, chunk.context_blurb,
                    )
                    if row is not None:
                        needs_embedding.add(str(row["chunk_id"]))
                elif existing["chunk_hash"] != chunk.chunk_hash:
                    await conn.execute(
                        """
                        UPDATE chunks SET text = $1, chunk_hash = $2, source_timestamp = $3,
                            context_blurb = $4, metadata = $5, superseded_at = NULL, tombstoned_at = NULL
                        WHERE chunk_id = $6
                        """,
                        chunk.text, chunk.chunk_hash, chunk.source_timestamp,
                        chunk.context_blurb, json.dumps(chunk.metadata), existing["chunk_id"],
                    )
                    needs_embedding.add(str(existing["chunk_id"]))
                # else: hash unchanged, no-op -- neither inserted nor re-embedded
            await conn.execute(
                "UPDATE chunks SET tombstoned_at = NOW() WHERE tenant_id = $1 AND doc_id = $2 "
                "AND chunk_index >= $3 AND tombstoned_at IS NULL",
                self._tenant_id, chunks[0].document_id if chunks else None, len(chunks),
            )
        return needs_embedding

    async def store_vectors(
        self,
        chunks: list[Chunk],
        embedding_model: str,
        dimensions: int = 4096,
        vectors_1024: Optional[dict[str, list[float]]] = None,
    ) -> int:
        """Insert chunk vectors. vectors_1024, if provided, is keyed by chunk_id (str)."""
        if not chunks:
            return 0
        count = 0
        async with tenant_connection(self._pool, self._tenant_id) as conn:
            for chunk in chunks:
                if chunk.embedding is None:
                    continue
                vector_str = "[" + ",".join(str(v) for v in chunk.embedding) + "]"
                v1024 = (vectors_1024 or {}).get(str(chunk.chunk_id))
                v1024_str = "[" + ",".join(str(v) for v in v1024) + "]" if v1024 else None
                await conn.execute(
                    """
                    INSERT INTO chunks_vectors (chunk_id, tenant_id, vector, vector_1024, embedding_model, dimensions)
                    VALUES ($1, $2, $3::vector, $4::vector, $5, $6)
                    ON CONFLICT (chunk_id) DO UPDATE
                    SET vector = $3::vector, vector_1024 = COALESCE($4::vector, chunks_vectors.vector_1024),
                        embedding_model = $5, dimensions = $6, embedded_at = NOW()
                    """,
                    chunk.chunk_id, self._tenant_id, vector_str, v1024_str, embedding_model, dimensions,
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

    async def store_relations(
        self,
        relations: list[Relation],
        model_version: Optional[str] = None,
        prompt_version: Optional[str] = None,
    ) -> int:
        """Insert relations into the relations table. Returns count.

        model_version/prompt_version label which extractor configuration
        produced these relations (read from graph.model_version/
        graph.prompt_version in config by the run_pipeline caller); None
        for relations added through a path with no extractor provenance
        (e.g. the direct add_relation graph-tool API).
        """
        if not relations:
            return 0
        count = 0
        async with tenant_connection(self._pool, self._tenant_id) as conn:
            for relation in relations:
                await conn.execute(
                    """
                    INSERT INTO relations (relation_id, tenant_id, source_entity_id,
                        target_entity_id, relation_type, weight, metadata,
                        chunk_id, confidence, extractor_id, model_version, prompt_version)
                    VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12)
                    ON CONFLICT (tenant_id, source_entity_id, target_entity_id, relation_type, chunk_id)
                    DO NOTHING
                    """,
                    relation.relation_id,
                    self._tenant_id,
                    relation.source_entity_id,
                    relation.target_entity_id,
                    relation.relation_type,
                    relation.weight if relation.weight else 1.0,
                    json.dumps(relation.metadata),
                    relation.chunk_id,
                    relation.confidence,
                    relation.extractor_id,
                    model_version,
                    prompt_version,
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

    ingest_store = PostgresIngestStore(pg_pool, tenant_id)
    file_hash = _sha256(text)

    document_id, unchanged = await ingest_store.store_document(document, file_hash=file_hash)
    if unchanged:
        return {
            "status": "skipped", "document_id": document_id, "path": path,
            "source_type": source_type, "reason": "unchanged", "errors": [],
        }
    document.document_id = document_id
    for chunk in chunks:
        chunk.document_id = document_id
        chunk.chunk_hash = _sha256(chunk.text)
        chunk.source_timestamp = (
            datetime.fromtimestamp(Path(path).stat().st_mtime, tz=timezone.utc)
            if is_file else datetime.now(timezone.utc)
        )

    if _contextual_enabled(config, source_type):
        from ..rag.contextualizer import ContextGenerator
        blurbs = ContextGenerator(config).generate_blurbs(text, [c.text for c in chunks])
        for chunk, blurb in zip(chunks, blurbs):
            chunk.context_blurb = blurb or None

    errors: list[str] = []
    pg_chunk_count = 0
    pg_vector_count = 0
    inserted_chunk_ids: set[str] = set()
    try:
        inserted_chunk_ids = await ingest_store.store_chunks(chunks)
        pg_chunk_count = len(inserted_chunk_ids)
    except Exception as exc:
        logging.warning("Postgres chunk write failed: %s", exc)
        errors.append(f"PostgresWriteError: {exc}")

    new_chunks = [c for c in chunks if str(c.chunk_id) in inserted_chunk_ids]

    embed_texts_override = {
        str(c.chunk_id): (f"{c.context_blurb}\n\n{c.text}" if c.context_blurb else c.text)
        for c in new_chunks
    }
    degraded, embed_err = embed_chunks(new_chunks, config, text_override=embed_texts_override)
    if embed_err is not None:
        logging.warning("embed_chunks: %s", embed_err)
        errors.append(f"EmbeddingError: {embed_err}")

    matryoshka_cfg = _nested_dict(config, "search")
    vectors_1024: dict[str, list[float]] = {}
    if bool(matryoshka_cfg.get("matryoshka_enabled", False)) and not degraded:
        from ..rag.embedder import _slice_normalize
        dim = int(matryoshka_cfg.get("matryoshka_dim", 1024))
        for c in new_chunks:
            if c.embedding is not None:
                vectors_1024[str(c.chunk_id)] = _slice_normalize(c.embedding, dim)

    try:
        embedding_model = str(_nested_dict(config, "embedding").get("model", "qwen3-embedding:8b"))
        dimensions = int(_nested_dict(config, "embedding").get("dimensions", 4096))
        pg_vector_count = await ingest_store.store_vectors(
            new_chunks, embedding_model, dimensions=dimensions, vectors_1024=vectors_1024 or None,
        )
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
                graph_cfg = _nested_dict(config, "graph")
                await ingest_store.store_relations(
                    relations,
                    model_version=graph_cfg.get("model_version"),
                    prompt_version=graph_cfg.get("prompt_version"),
                )
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
        await ingest_store.store_document(document, file_hash=file_hash)
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


async def ingest_text(
    text: str,
    pg_pool: asyncpg.Pool,
    source_type: str = "text",
    config: Optional[dict[str, object]] = None,
    tenant_id: str = DEFAULT_TENANT_ID,
    source: str = "raw_text",
) -> dict[str, object]:
    """Ingest raw text with optional type hint and source identifier.

    Lives here (rather than only in ``ingest_tools``) so tests and other
    ingest-side code can import it alongside ``run_pipeline`` without a
    circular import; ``ingest_tools.ingest_text`` re-exports this function.
    """
    if source_type not in {"code", "markdown", "text"}:
        return {"status": "error", "message": f"Invalid source_type: {source_type}"}
    config = load_config_or_pass(config)
    return await run_pipeline(
        text, source_type, source, config, pg_pool, tenant_id, is_file=False
    )
