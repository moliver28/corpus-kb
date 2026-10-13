"""Ingest pipeline orchestration: partition, chunk, embed, extract, store.

All writes go directly to Postgres via asyncpg (see ``ingest_store`` for the
write layer and the ``ingest_runs`` integrity ledger). No LanceDB, no SQLite.
The ingest pipeline is async — callers must await run_pipeline().
"""

from __future__ import annotations

import hashlib
import logging
from datetime import UTC, datetime
from pathlib import Path

import asyncpg

from ..chunking.unstructured_chunker import chunk_elements
from ..config import load_config
from ..extraction import create_extractor
from ..extraction.pgml_backend import PgmlExtractor
from ..ingest.hierarchy_settings import load_hierarchy_settings
from ..ontology import Ontology, load_ontology
from ..partitioning import ElementProxy
from ..partitioning import partition as unstructured_partition
from ..rag import aembed_batch, create_embedder
from ..storage.rag_backend import RagBackend
from ..utils.models import Chunk, Document, Entity, Relation
from .ingest_store import DEFAULT_TENANT_ID, PostgresIngestStore

logger = logging.getLogger(__name__)


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def load_config_or_pass(config: dict[str, object] | None) -> dict[str, object]:
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
    return [ElementProxy(text=text, element_type="NarrativeText", element_id="raw-text")]


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


def _all_zero(vectors: list[list[float]]) -> bool:
    """Return True when every vector is entirely zeros (embedder failure marker)."""
    return all(all(v == 0.0 for v in vector) for vector in vectors)


def _embedding_failure_is_fatal(chunks: list[Chunk]) -> bool:
    """True when the embed output must fail the run rather than degrade it.

    Missing or all-zero embeddings are the embedder-failure marker: they would
    poison the search corpus, so the run records the EmbeddingError (and the
    ledger marks the run failed). A degraded run whose fallback provider
    produced real vectors stays error-free — the degraded flag carries the
    honesty, and the note is reported as ``embed_note`` instead.
    """
    embeddings = [c.embedding for c in chunks if c.embedding is not None]
    if not embeddings:
        return True
    return any(all(v == 0.0 for v in vector) for vector in embeddings)


def _assign_embeddings(chunks: list[Chunk], vectors: list[list[float]]) -> None:
    for chunk, vector in zip(chunks, vectors, strict=True):
        chunk.embedding = vector


async def embed_chunks(
    chunks: list[Chunk],
    config: dict[str, object],
    pool: asyncpg.Pool | None = None,
    text_override: dict[str, str] | None = None,
) -> tuple[bool, str | None]:
    """Embed chunk texts via the configured provider, returning (degraded, error).

    The primary embedder is selected by ``embedding.provider`` (default
    ``"pgml"``). When the primary returns zero-vectors, the
    ``embedding.fallback_provider`` embedder (default ``"ollama"``) is tried
    and its vectors repopulate the chunks. ``degraded`` is True whenever the
    primary failed — even when the fallback produced real vectors — so the
    flag always reflects which embedder actually produced the embeddings.

    On success, each chunk's ``embedding`` field is populated. On failure,
    returns ``(True, "ExceptionType: message")`` so the caller can report
    structured error info. ``text_override``, if provided, is keyed by
    chunk_id (str) and takes precedence over ``chunk.text`` -- used to embed
    the contextualized (blurb + text) form instead of raw chunk text.
    """
    try:
        embedding_cfg = _nested_dict(config, "embedding")
        provider_value = embedding_cfg.get("provider")
        provider = provider_value if isinstance(provider_value, str) else "pgml"

        embedder = create_embedder(config, pool)
        texts = [(text_override or {}).get(str(chunk.chunk_id), chunk.text) for chunk in chunks]
        vectors = await aembed_batch(embedder, texts)

        if vectors and _all_zero(vectors):
            fallback_value = embedding_cfg.get("fallback_provider")
            fallback_provider = fallback_value if isinstance(fallback_value, str) else "ollama"
            if fallback_provider == provider:
                _assign_embeddings(chunks, vectors)
                return (
                    True,
                    f"Connection failed: {type(embedder).__name__} returned zero vectors",
                )
            fallback_config = dict(config)
            fallback_embedding = dict(embedding_cfg)
            fallback_embedding["provider"] = fallback_provider
            fallback_config["embedding"] = fallback_embedding
            fallback_embedder = create_embedder(fallback_config, pool)
            fallback_vectors = await aembed_batch(fallback_embedder, texts)
            if fallback_vectors and not _all_zero(fallback_vectors):
                _assign_embeddings(chunks, fallback_vectors)
                return (
                    True,
                    f"Connection failed: {provider} returned zero vectors; "
                    f"embeddings produced by fallback {fallback_provider}",
                )
            _assign_embeddings(chunks, vectors)
            return (
                True,
                f"Connection failed: {provider} and fallback "
                f"{fallback_provider} both returned zero vectors",
            )

        _assign_embeddings(chunks, vectors)
        return False, None
    except Exception as exc:
        return True, f"{type(exc).__name__}: {exc}"


def _extractor_name(config: dict[str, object]) -> str:
    graph = _nested_dict(config, "graph")
    raw = graph.get("extractor")
    return raw if isinstance(raw, str) else "pgml"


async def extract_with_fallback(
    chunks: list[Chunk],
    ontology: Ontology,
    source_document_id: str,
    config: dict[str, object],
    pool: asyncpg.Pool | None = None,
) -> tuple[list[Entity], list[Relation], str]:
    """Extract entities and relations, falling back through the extractor chain.

    Order depends on ``graph.extractor``:

    - ``pgml``: PostgresML NER -> LangExtract -> RegexExtractor
    - ``langextract``: LangExtract -> RegexExtractor
    - ``regex``: RegexExtractor only

    The returned ``extractor_id`` always names the extractor that actually
    produced the output.
    """
    extractor_name = _extractor_name(config)

    # Reject unknown extractor names explicitly so we never silently fall back
    # to regex when the user configured something unsupported.
    if extractor_name not in {"pgml", "langextract", "regex"}:
        create_extractor(config, pool=pool)

    if extractor_name == "pgml":
        try:
            pgml_extractor = create_extractor(config, pool=pool)
            if isinstance(pgml_extractor, PgmlExtractor):
                entities, relations = await pgml_extractor.aextract(
                    chunks, ontology, source_document_id
                )
            else:
                entities, relations = pgml_extractor.extract(chunks, ontology, source_document_id)
            if entities:
                return (
                    entities,
                    relations,
                    _actual_extractor_id(entities, pgml_extractor.extractor_id),
                )
        except Exception:
            pass

    if extractor_name in ("pgml", "langextract"):
        try:
            lang_extractor = create_extractor(
                {
                    **config,
                    "graph": {
                        **_nested_dict(config, "graph"),
                        "extractor": "langextract",
                    },
                }
            )
            entities, relations = lang_extractor.extract(chunks, ontology, source_document_id)
            if entities:
                return (
                    entities,
                    relations,
                    _actual_extractor_id(entities, lang_extractor.extractor_id),
                )
        except (FileNotFoundError, ImportError, ModuleNotFoundError):
            pass

    from ..extraction import RegexExtractor

    fallback = RegexExtractor()
    entities, relations = fallback.extract(chunks, ontology, source_document_id)
    return entities, relations, _actual_extractor_id(entities, fallback.extractor_id)


def _actual_extractor_id(entities: list[Entity], fallback_id: str) -> str:
    """Return the extractor_id that actually produced the entities.

    Extractors that internally fall back to RegexExtractor may leave
    entities with a different ``extractor_id`` than the wrapper's own id.
    When every entity agrees on a different id, report that id honestly.
    """
    if not entities:
        return fallback_id
    ids = {entity.extractor_id for entity in entities if entity.extractor_id}
    if len(ids) == 1:
        return ids.pop()
    return fallback_id


def _extract_entities_flag(config: dict[str, object]) -> bool:
    graph = _nested_dict(config, "graph")
    flag = graph.get("extract_entities")
    return flag if isinstance(flag, bool) else True


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

    Integrity semantics (migration 022):

    - A per-(tenant, content-hash) Postgres advisory lock serializes
      concurrent ingests of the same content; contention returns an honest
      ``{"status": "skipped", "reason": "concurrent-run"}`` — never success.
    - Every run opens an ``ingest_runs`` ledger row (status 'running') before
      any stage writes and closes it after the last one. The unchanged
      short-circuit only fires for content whose last run COMPLETED with an
      empty stage error list, so a crashed partial ingest is never enshrined
      as success.

    Args:
        text: The full text content to ingest.
        source_type: One of "code", "markdown", "text".
        path: Source path or a custom source identifier.
        config: Pipeline config dict.
        pg_pool: asyncpg connection pool for Postgres writes.
        tenant_id: Tenant ID for RLS.
        is_file: True if path points to a file; False if path is raw text source ID.

    Returns:
        Result dict with keys: status ("success" when every stage was clean,
        "partial" when any stage recorded an error, "skipped" for the
        dedupe/concurrency short-circuits), document_id, path, source_type,
        size_bytes, chunk_count, entity_count, relation_count,
        pg_chunk_count, pg_vector_count, degraded, extractor_id, entities,
        errors, run_id (and embed_note when a fallback embedder rescued the
        embeddings).
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
    file_hash = _sha256(text)

    # Serialize concurrent ingests of the same content per tenant. The lock is
    # session-scoped on its own pooled connection, so a crashed process
    # releases it server-side automatically.
    async with pg_pool.acquire() as lock_conn:
        acquired = await lock_conn.fetchval(
            "SELECT pg_try_advisory_lock(hashtextextended($1 || ':' || $2, 0))",
            tenant_id,
            file_hash,
        )
        if not acquired:
            return {
                "status": "skipped",
                "reason": "concurrent-run",
                "path": path,
                "source_type": source_type,
                "errors": [],
            }
        try:
            return await _run_locked_stages(
                document=document,
                chunks=chunks,
                file_hash=file_hash,
                text=text,
                source_type=source_type,
                path=path,
                config=config,
                pg_pool=pg_pool,
                tenant_id=tenant_id,
                rag_backend=rag_backend,
                is_file=is_file,
            )
        finally:
            await lock_conn.execute(
                "SELECT pg_advisory_unlock(hashtextextended($1 || ':' || $2, 0))",
                tenant_id,
                file_hash,
            )


async def _run_locked_stages(
    document: Document,
    chunks: list[Chunk],
    file_hash: str,
    text: str,
    source_type: str,
    path: str,
    config: dict[str, object],
    pg_pool: asyncpg.Pool,
    tenant_id: str,
    rag_backend: RagBackend | None,
    is_file: bool,
) -> dict[str, object]:
    """Pipeline stages under the advisory lock: open the ledger, store, close."""
    ingest_store = PostgresIngestStore(pg_pool, tenant_id)

    unchanged_doc = await ingest_store.unchanged_doc_id(document.path, file_hash)
    if unchanged_doc is not None:
        return {
            "status": "skipped",
            "document_id": unchanged_doc,
            "path": path,
            "source_type": source_type,
            "reason": "unchanged",
            "errors": [],
        }

    document_id, run_id = await ingest_store.begin_run(document, file_hash)
    try:
        document.document_id = document_id
        for chunk in chunks:
            chunk.document_id = document_id
            chunk.chunk_hash = _sha256(chunk.text)
            chunk.source_timestamp = (
                datetime.fromtimestamp(Path(path).stat().st_mtime, tz=UTC)
                if is_file
                else datetime.now(UTC)
            )

        if _contextual_enabled(config, source_type):
            from ..rag.contextualizer import ContextGenerator

            blurbs = ContextGenerator(config).generate_blurbs(text, [c.text for c in chunks])
            for chunk, blurb in zip(chunks, blurbs, strict=True):
                chunk.context_blurb = blurb or None

        errors: list[str] = []
        embed_note: str | None = None
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
        degraded, embed_err = await embed_chunks(
            new_chunks, config, pool=pg_pool, text_override=embed_texts_override
        )
        if embed_err is not None:
            if _embedding_failure_is_fatal(new_chunks):
                logging.warning("embed_chunks: %s", embed_err)
                errors.append(f"EmbeddingError: {embed_err}")
            else:
                # The fallback provider produced real vectors: degraded, but
                # everything stored. A note, NOT an error — otherwise no run
                # on a fallback-dependent host would ever count as completed
                # and the dedupe probe would never fire.
                embed_note = embed_err

        matryoshka_cfg = _nested_dict(config, "search")
        vectors_1024: dict[str, list[float]] = {}
        if bool(matryoshka_cfg.get("matryoshka_enabled", False)) and not degraded:
            from ..rag.embedder import _slice_normalize

            dim = int(matryoshka_cfg.get("matryoshka_dim", 1024))
            for c in new_chunks:
                if c.embedding is not None:
                    vectors_1024[str(c.chunk_id)] = _slice_normalize(c.embedding, dim)

        try:
            embedding_model = str(
                _nested_dict(config, "embedding").get("model", "nomic-embed-text")
            )
            dimensions = int(_nested_dict(config, "embedding").get("dimensions", 768))
            pg_vector_count = await ingest_store.store_vectors(
                new_chunks,
                embedding_model,
                dimensions=dimensions,
                vectors_1024=vectors_1024 or None,
            )
        except Exception as exc:
            logging.warning("Postgres vector write failed: %s", exc)
            errors.append(f"PostgresWriteError: {exc}")
            pg_chunk_count = 0
            pg_vector_count = 0

        # U48/P6: side-by-side SHADOW hierarchy — additive only. The v1
        # chunks/vectors above stay the live search corpus; any shadow failure
        # is logged and isolated so it can never break the live ingest.
        hierarchy_settings = load_hierarchy_settings(config)
        shadow_chunk_count = 0
        shadow_vector_count = 0
        shadow_note: str | None = None
        if hierarchy_settings.enabled:
            try:
                from ..tools.shadow_ingest import write_shadow_hierarchy

                shadow_chunk_count, shadow_vector_count, shadow_note = await write_shadow_hierarchy(
                    pool=pg_pool,
                    tenant_id=tenant_id,
                    document_id=document.document_id,
                    text=text,
                    chunks=chunks,
                    config=config,
                    settings=hierarchy_settings,
                )
            except Exception as exc:
                logging.warning("Shadow hierarchy write failed: %s", exc)
                errors.append(f"ShadowWriteError: {exc}")

        # Extract entities and relations
        entities: list[Entity] = []
        relations: list[Relation] = []
        extractor_id = "none"
        if _extract_entities_flag(config):
            try:
                entities, relations, extractor_id = await extract_with_fallback(
                    chunks, ontology(config), document.document_id, config, pool=pg_pool
                )
                if entities:
                    await ingest_store.store_entities(entities)
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
            await ingest_store.update_document(document, file_hash)
        except Exception as exc:
            logging.warning("Document update failed: %s", exc)
            errors.append(f"PostgresWriteError: {exc}")

        stages: dict[str, object] = {
            "chunks": pg_chunk_count,
            "vectors": pg_vector_count,
            "entities": len(entities),
            "relations": len(relations),
            "degraded": degraded,
            "extractor_id": extractor_id,
            "errors": errors,
        }
        if hierarchy_settings.enabled:
            stages["shadow_chunk_count"] = shadow_chunk_count
            stages["shadow_vector_count"] = shadow_vector_count

        # Close the ledger LAST — completed only when no stage recorded an
        # error; failed with the error text otherwise.
        await ingest_store.finish_run(run_id, file_hash, stages, errors)

        result: dict[str, object] = {
            # Nothing reports success on a partial ingest: any stage error
            # flips the status (the ledger row is 'failed' either way).
            "status": "success" if not errors else "partial",
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
            "run_id": run_id,
        }
        if embed_note is not None:
            result["embed_note"] = embed_note
        # Shadow keys are present ONLY when the hierarchy flag is on, so the
        # flag-off result dict stays byte-identical to the pre-U48 shape.
        if hierarchy_settings.enabled:
            result["shadow_chunk_count"] = shadow_chunk_count
            result["shadow_vector_count"] = shadow_vector_count
            if shadow_note is not None:
                result["shadow_note"] = shadow_note
        return result
    except Exception as exc:
        await ingest_store.fail_run(run_id, f"{type(exc).__name__}: {exc}")
        raise


async def ingest_text(
    text: str,
    pg_pool: asyncpg.Pool,
    source_type: str = "text",
    config: dict[str, object] | None = None,
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
    return await run_pipeline(text, source_type, source, config, pg_pool, tenant_id, is_file=False)
