"""Ingest pipeline orchestration: partition, chunk, embed, extract, store.

All writes go directly to Postgres via asyncpg (see ``ingest_store`` for the
write layer and the ``ingest_runs`` integrity ledger). No LanceDB, no SQLite.
The ingest pipeline is async — callers must await run_pipeline().
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

import asyncpg

from ..config import load_config
from ..extraction import ExtractStats, create_extractor
from ..extraction.pgml_backend import PgmlExtractor
from ..ingest.hierarchy_settings import load_hierarchy_settings
from ..ingestion.base import (
    DETECT_HEAD_CHARS,
    PlanConformanceError,
    SourceProfile,
    chunks_from_plan,
    validate_plan,
)
from ..ingestion.registry import resolve_strategy
from ..ontology import Ontology, load_ontology
from ..rag import aembed_batch, create_embedder
from ..rag.embedder_selection import (
    POOLED_MODE,
    IngestEmbeddingPlan,
    late_chunking_requested,
    select_ingest_embedder,
)
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


def build_embedding_input(text: str, context_blurb: str | None) -> str:
    """Single-source the embedding-input assembly (Stage 3).

    Chunks with a contextual blurb embed the ``blurb\n\ntext`` form; plain
    chunks embed raw text. The reembed helper (reembed_degraded) and the
    ingest pipeline MUST produce byte-identical inputs for the same chunk or
    a repair would store a vector for a different text than a fresh ingest
    would.
    """
    return f"{context_blurb}\n\n{text}" if context_blurb else text


def _assign_embeddings(chunks: list[Chunk], vectors: list[list[float]]) -> None:
    for chunk, vector in zip(chunks, vectors, strict=True):
        chunk.embedding = vector


async def embed_chunks(
    chunks: list[Chunk],
    config: dict[str, object],
    pool: asyncpg.Pool | None = None,
    text_override: dict[str, str] | None = None,
    document_text: str | None = None,
    meta: dict[str, object] | None = None,
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

    Late chunking (DOCUMENT ingest only): when the activation contract holds
    (``embedding.late_chunking`` + latechunk extra + qwen3-embedding model —
    see ``rag.embedder_selection.select_ingest_embedder``) and
    ``document_text`` is provided, chunks carrying source char spans embed
    via ``embed_document_chunks`` against the document (every chunk vector
    pools token states that saw the whole document); chunks lacking spans
    embed pooled with a per-chunk note. ``meta``, when given, receives
    ``"embedding_mode"`` always and ``"embedding_note"`` whenever a fallback
    note exists, so callers can surface the decision on the run result.
    """
    try:
        if not chunks:
            if meta is not None:
                meta["embedding_mode"] = POOLED_MODE
            return False, None

        if document_text is None:
            plan: IngestEmbeddingPlan | None = None
            mode = POOLED_MODE
            # Honest fallback when late chunking was requested but the caller
            # could not supply the document (e.g. the reembed repair path).
            note = (
                "late_chunking requested but no document text was provided to "
                "embed_chunks; pooled fallback"
                if late_chunking_requested(config)
                else None
            )
        else:
            plan = select_ingest_embedder(config, pool=pool)
            mode = plan.mode
            note = plan.note

        if plan is not None and plan.spans_provider is not None:
            assert document_text is not None  # plan only exists with document text
            # The late path runs synchronous HF/torch forwards; offload to a
            # worker thread so the server event loop (uvicorn + socket share
            # it) never blocks behind a multi-second window forward.
            vectors, span_note, spans_used = await asyncio.to_thread(
                _late_chunk_vectors, plan, chunks, document_text, text_override
            )
            if span_note is not None:
                note = f"{note}; {span_note}" if note else span_note
            if meta is not None:
                # Mode honesty: "late-chunking" only when at least one chunk
                # was actually late-chunked; an all-pooled run with the flag
                # on is reported as pooled (the note carries the reason).
                meta["embedding_mode"] = mode if spans_used > 0 else POOLED_MODE
                if note is not None:
                    meta["embedding_note"] = note
            if vectors and _all_zero(vectors):
                _assign_embeddings(chunks, vectors)
                return True, "Connection failed: LateChunkEmbedder returned zero vectors"
            _assign_embeddings(chunks, vectors)
            return False, None

        if meta is not None:
            meta["embedding_mode"] = mode
            if note is not None:
                meta["embedding_note"] = note

        embedding_cfg = _nested_dict(config, "embedding")
        provider_value = embedding_cfg.get("provider")
        provider = provider_value if isinstance(provider_value, str) else "pgml"

        embedder = plan.embedder if plan is not None else create_embedder(config, pool)
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


def _late_chunk_vectors(
    plan: IngestEmbeddingPlan,
    chunks: list[Chunk],
    document_text: str,
    text_override: dict[str, str] | None,
) -> tuple[list[list[float]], str | None, int]:
    """Late-chunked vectors for span-carrying chunks, pooled for the rest.

    Chunks whose (source_start_char, source_end_char) span is a valid range
    into ``document_text`` embed via the plan's ``embed_document_chunks``
    (true late chunking — the contextualized blurb form is NOT used there,
    since spans point into the raw document). A span is only trusted when
    ``document_text[start:end]`` reproduces the chunk text verbatim — a
    fabricated or drifted span would pool an unrelated region while still
    labeling the vector late-chunked, so it falls back to pooled instead.
    Chunks without a trusted span embed via the plan embedder's
    ``embed_batch`` (single-span forward per text) with a per-chunk note so
    the fallback is never silent. Returns ``(vectors, note, spans_used)``;
    the caller reports the late-chunking mode only when ``spans_used > 0``.
    """
    spans_provider = plan.spans_provider
    assert spans_provider is not None
    late_idx: list[int] = []
    spans: list[tuple[int, int]] = []
    pooled_idx: list[int] = []
    for idx, chunk in enumerate(chunks):
        start, end = chunk.source_start_char, chunk.source_end_char
        if (
            isinstance(start, int)
            and isinstance(end, int)
            and 0 <= start < end <= len(document_text)
            and document_text[start:end] == chunk.text
        ):
            late_idx.append(idx)
            spans.append((start, end))
        else:
            pooled_idx.append(idx)
    vectors: list[list[float]] = [[] for _ in chunks]
    note: str | None = None
    if late_idx:
        chunk_spans = spans_provider(document_text, spans)
        for idx, span in zip(late_idx, chunk_spans, strict=True):
            vectors[idx] = span.vector
    if pooled_idx:
        texts = [
            (text_override or {}).get(str(chunks[idx].chunk_id), chunks[idx].text)
            for idx in pooled_idx
        ]
        pooled = plan.embedder.embed_batch(texts)
        for idx, vector in zip(pooled_idx, pooled, strict=True):
            vectors[idx] = vector
        note = (
            f"late chunking: {len(pooled_idx)}/{len(chunks)} chunks lacked a trusted "
            "source char span; pooled fallback for those"
        )
    return vectors, note, len(late_idx)


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
) -> tuple[list[Entity], list[Relation], str, ExtractStats]:
    """Extract entities and relations, falling back through the extractor chain.

    Order depends on ``graph.extractor``:

    - ``pgml``: PostgresML NER -> LangExtract -> RegexExtractor
    - ``langextract``: LangExtract -> RegexExtractor
    - ``gliner``: GLiNER2 NER -> RegexExtractor
    - ``regex``: RegexExtractor only

    The returned ``extractor_id`` always names the extractor that actually
    produced the output. Every failure and every empty result along the
    chain is recorded in ``stats.notes`` — a fallback is never silent.

    Returns:
        (entities, relations, extractor_id, stats).
    """
    extractor_name = _extractor_name(config)
    failures: list[str] = []

    # Reject unknown extractor names explicitly so we never silently fall back
    # to regex when the user configured something unsupported.
    if extractor_name not in {"pgml", "langextract", "regex", "gliner"}:
        create_extractor(config, pool=pool)

    if extractor_name == "gliner":
        try:
            gliner_extractor = create_extractor(config, pool=pool)
            entities, relations, stats = gliner_extractor.extract(
                chunks, ontology, source_document_id
            )
            if entities:
                return (
                    entities,
                    relations,
                    _actual_extractor_id(entities, gliner_extractor.extractor_id),
                    stats,
                )
            failures.append("gliner returned no entities")
            logger.warning("Gliner extraction returned no entities; falling back to regex.")
        except ValueError:
            # Configuration error (e.g. gliner not enabled, unknown mention
            # type): propagate loudly instead of silently downgrading the
            # user's explicit extractor choice to regex.
            raise
        except Exception as exc:
            failures.append(f"gliner: {type(exc).__name__}: {exc}")
            logger.warning("Gliner extraction failed: %s; falling back to regex.", exc)

    if extractor_name == "pgml":
        try:
            pgml_extractor = create_extractor(config, pool=pool)
            if isinstance(pgml_extractor, PgmlExtractor):
                entities, relations, stats = await pgml_extractor.aextract(
                    chunks, ontology, source_document_id
                )
            else:
                entities, relations, stats = pgml_extractor.extract(
                    chunks, ontology, source_document_id
                )
            if entities:
                return (
                    entities,
                    relations,
                    _actual_extractor_id(entities, pgml_extractor.extractor_id),
                    stats,
                )
            failures.append("pgml returned no entities")
            logger.warning(
                "PostgresML extraction returned no entities; falling back to langextract."
            )
        except Exception as exc:
            failures.append(f"pgml: {type(exc).__name__}: {exc}")
            logger.warning("PostgresML extraction failed: %s; falling back to langextract.", exc)

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
            entities, relations, stats = lang_extractor.extract(
                chunks, ontology, source_document_id
            )
            if entities:
                return (
                    entities,
                    relations,
                    _actual_extractor_id(entities, lang_extractor.extractor_id),
                    stats,
                )
            failures.append("langextract returned no entities")
            logger.warning("LangExtract extraction returned no entities; falling back to regex.")
        except Exception as exc:
            failures.append(f"langextract: {type(exc).__name__}: {exc}")
            logger.warning("LangExtract extraction failed: %s; falling back to regex.", exc)

    from ..extraction import RegexExtractor

    fallback = RegexExtractor()
    entities, relations, stats = fallback.extract(chunks, ontology, source_document_id)
    if failures:
        stats = replace(stats, notes=stats.notes + tuple(failures))
    return entities, relations, _actual_extractor_id(entities, fallback.extractor_id), stats


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
    strategy_name: str | None = None,
) -> dict[str, object]:
    """Run the full ingest pipeline: resolve strategy, build chunks, embed,
    extract, store.

    The strategy is resolved ONCE per document (precedence: explicit
    ``strategy_name`` override > ``ingestion.strategies[source_type]`` config
    mapping > content detection > ``ingestion.default``); its ChunkPlan
    replaces the partition+chunk step. Everything downstream (embed ->
    extract -> store) is strategy-agnostic.

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
        source_type: Document source type ("code", "markdown", "text",
            "transcript", ...) — also the config-mapping and detection hint.
        path: Source path or a custom source identifier.
        config: Pipeline config dict.
        pg_pool: asyncpg connection pool for Postgres writes.
        tenant_id: Tenant ID for RLS.
        is_file: True if path points to a file; False if path is raw text source ID.
        strategy_name: Optional explicit ingestion-strategy override (a
            registered strategy name). Unknown names raise UnknownStrategyError.

    Returns:
        Result dict with keys: status ("success" when every stage was clean,
        "partial" when any stage recorded an error, "skipped" for the
        dedupe/concurrency short-circuits), document_id, path, source_type,
        size_bytes, chunk_count, entity_count, relation_count,
        pg_chunk_count, pg_vector_count, degraded, extractor_id, entities,
        errors, run_id (and embed_note when a fallback embedder rescued the
        embeddings) — plus the strategy provenance fields ``strategy``,
        ``strategy_basis``, ``detection_confidence``, ``detection_reason``
        (and ``warnings`` when non-empty).
    """
    resolution = resolve_strategy(
        source_type,
        Path(path).name,
        text[:DETECT_HEAD_CHARS],
        config,
        strategy_override=strategy_name,
    )
    profile = SourceProfile(source_type=source_type, is_file=is_file, path=path)
    plan = resolution.strategy.build_chunks(text, Path(path).name, profile)
    # Conformance escalation (Section 0): a non-conformant plan would
    # silently tombstone or corrupt rows downstream (ingest_store tombstones
    # chunk_index >= len(chunks)), so it FAILS here — before the advisory
    # lock and the ledger, while nothing has been written. plan.warnings
    # stays the soft channel for strategy self-reported notes.
    conformance_problems = validate_plan(plan)
    if conformance_problems:
        raise PlanConformanceError(
            f"ingestion strategy {resolution.strategy.name!r} produced a "
            "non-conformant plan: " + "; ".join(conformance_problems)
        )
    if resolution.detection is not None:
        plan.detection_confidence = resolution.detection.confidence
        plan.detection_reason = resolution.detection.reason
    # For files the strategy derives the document text from the parsed
    # content (previously the unstructured partition step did); binary
    # formats work because the strategy partitions the FILE, never decoding
    # bytes as UTF-8 first.
    text = plan.document_text
    document = build_document(path, source_type, text)
    chunks = chunks_from_plan(plan, document.document_id)
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
            result = await _run_locked_stages(
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
                empty_reason=plan.empty_reason,
            )
            # Strategy provenance (plan Section 0): which seam produced the
            # chunks. Attached here because resolution/plan live in this scope.
            result["strategy"] = resolution.strategy.name
            result["strategy_basis"] = resolution.basis
            if resolution.detection is not None:
                result["detection_confidence"] = round(resolution.detection.confidence, 4)
                result["detection_reason"] = resolution.detection.reason
            if resolution.detected is not None:
                result["detected_strategy"] = resolution.detected
            if plan.warnings:
                result["warnings"] = list(plan.warnings)
            return result
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
    empty_reason: str | None = None,
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
        # A zero-chunk plan must never complete clean: the EmptyPlanError
        # seeds the stage error list BEFORE finish_run, so the ledger closes
        # failed and the unchanged-probe can never enshrine an empty run as
        # success. The strategy's empty_reason (when it gave one) is
        # surfaced verbatim in the result dict.
        errors: list[str] = []
        if not chunks:
            errors.append(f"EmptyPlanError: {empty_reason or 'strategy produced no chunks'}")
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
            str(c.chunk_id): build_embedding_input(c.text, c.context_blurb) for c in new_chunks
        }
        embed_meta: dict[str, object] = {}
        degraded, embed_err = await embed_chunks(
            new_chunks,
            config,
            pool=pg_pool,
            text_override=embed_texts_override,
            document_text=text,
            meta=embed_meta,
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
        stats = ExtractStats(backend="none", notes=("extraction disabled",))
        if _extract_entities_flag(config):
            try:
                entities, relations, extractor_id, stats = await extract_with_fallback(
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
                stats = ExtractStats(
                    backend=extractor_id,
                    notes=(f"extraction failed: {type(exc).__name__}: {exc}",),
                )

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
            # Honest extraction accounting: requested vs kept vs where the
            # difference went, plus failure notes from the fallback chain.
            "extraction": {
                "extractor_id": extractor_id,
                "requested_entities": stats.requested_entities,
                "kept_entities": stats.kept_entities,
                "requested_relations": stats.requested_relations,
                "kept_relations": stats.kept_relations,
                "dropped_by_cap": stats.dropped_by_cap,
                "dropped_by_confidence": stats.dropped_by_confidence,
                "dropped_by_other": stats.dropped_by_other,
                "duration_ms": round(stats.duration_ms, 3),
                "notes": list(stats.notes),
            },
            "entities": {entity.name: entity.entity_id for entity in entities},
            "errors": errors,
            "run_id": run_id,
        }
        if embed_note is not None:
            result["embed_note"] = embed_note
        # Honest embedding accounting (late-chunking wiring): WHICH technique
        # produced the vectors, and — only when late chunking was requested
        # but unavailable — the concrete reason it fell back.
        result["embedding_mode"] = str(embed_meta.get("embedding_mode", POOLED_MODE))
        if embed_meta.get("embedding_note") is not None:
            result["embedding_note"] = embed_meta["embedding_note"]
        if empty_reason is not None:
            result["empty_reason"] = empty_reason
        # Shadow keys are present ONLY when the hierarchy flag is on, so the
        # flag-off result dict stays byte-identical to the pre-U48 shape.
        if hierarchy_settings.enabled:
            result["shadow_chunk_count"] = shadow_chunk_count
            result["shadow_vector_count"] = shadow_vector_count
            if shadow_note is not None:
                result["shadow_note"] = shadow_note
        return result
    except BaseException as exc:
        # BaseException, not Exception: asyncio.CancelledError (watch-mode
        # Ctrl-C) is a BaseException in 3.11+ and must still close the
        # ledger row — a run cancelled mid-flight must never strand at
        # 'running'. fail_run is best-effort and never raises; the
        # cancellation (or any other BaseException) propagates unchanged.
        await ingest_store.fail_run(run_id, f"{type(exc).__name__}: {exc}")
        raise


async def reembed_degraded(
    pg_pool: asyncpg.Pool,
    config: dict[str, object],
    tenant_id: str = DEFAULT_TENANT_ID,
    limit: int | None = None,
    doc_id: str | None = None,
) -> dict[str, object]:
    """Re-embed chunks whose stored vector is flagged degraded (Stage 3).

    Targets rows where ``chunks_vectors.degraded`` or ``chunks.degraded`` is
    TRUE (the migration-022 flags), rebuilds each embedding input with
    ``build_embedding_input`` (the exact bytes a fresh ingest would embed),
    and on a non-zero result writes the repaired vector and CLEARS the
    degraded flags. All-zero output leaves the row flagged — a repair that
    produced the degraded-mode marker is a failure, never a silent "fix".

    Requires migration 022; on a pre-migration database the degraded columns
    do not exist, so this returns ``{"status": "unavailable", ...}`` instead
    of crashing. CLI/surface registration is Stage 5; this is the handler.

    Returns an honest ``{scanned, fixed, failed}`` summary.
    """
    async with pg_pool.acquire() as conn, conn.transaction():
        # set_config FIRST inside the transaction: a LOCAL GUC issued on an
        # autocommit connection evaporates with its statement, so the reads
        # below would run GUC-less and match zero rows on FORCE-RLS
        # deployments (the ingest_store ledger-method pattern).
        await conn.execute("SELECT set_config('app.current_tenant_id', $1, true)", tenant_id)
        present = await conn.fetchval(
            """
            SELECT count(*) FROM information_schema.columns
            WHERE table_schema = 'public' AND table_name = ANY($1::text[])
              AND column_name = 'degraded'
            """,
            ["chunks", "chunks_vectors"],
        )
        if present != 2:
            return {
                "status": "unavailable",
                "reason": (
                    "migration 022 not applied: chunks.degraded / "
                    "chunks_vectors.degraded missing; run corpus-kb setup"
                ),
                "scanned": 0,
                "fixed": 0,
                "failed": 0,
            }
        rows = await conn.fetch(
            """
            SELECT cv.chunk_id::text AS chunk_id, c.doc_id::text AS doc_id,
                   c.text, c.context_blurb
            FROM chunks_vectors cv
            JOIN chunks c ON c.chunk_id = cv.chunk_id
            WHERE cv.tenant_id = $1 AND (cv.degraded = TRUE OR c.degraded = TRUE)
              AND ($2::uuid IS NULL OR c.doc_id = $2::uuid)
            ORDER BY cv.embedded_at NULLS LAST
            LIMIT $3
            """,
            tenant_id,
            doc_id,
            limit if limit is not None else 2**31 - 1,
        )
    if not rows:
        return {"status": "success", "scanned": 0, "fixed": 0, "failed": 0}

    # Reuse the pipeline's embed path (provider + zero-vector fallback chain)
    # by rebuilding minimal Chunk objects; text_override keys by chunk_id.
    chunks = [
        Chunk(
            chunk_id=row["chunk_id"],
            document_id=row["doc_id"],
            text=row["text"],
            source_type="text",
            context_blurb=row["context_blurb"],
        )
        for row in rows
    ]
    text_override = {c.chunk_id: build_embedding_input(c.text, c.context_blurb) for c in chunks}
    # meta passthrough: a repair re-embeds WITHOUT the document, so when late
    # chunking is the configured technique the repaired chunk lands in the
    # pooled vector space while its siblings stay late-chunked — surface the
    # mode/note in the summary instead of silently mixing spaces.
    embed_meta: dict[str, object] = {}
    degraded, embed_err = await embed_chunks(
        chunks, config, pool=pg_pool, text_override=text_override, meta=embed_meta
    )
    if embed_err is not None:
        logger.warning("reembed_degraded: %s", embed_err)

    embedding_cfg = _nested_dict(config, "embedding")
    embedding_model = str(embedding_cfg.get("model", "nomic-embed-text"))
    raw_dimensions = embedding_cfg.get("dimensions", 768)
    dimensions = raw_dimensions if isinstance(raw_dimensions, int) else 768
    matryoshka_cfg = _nested_dict(config, "search")
    v1024_dim: int | None = None
    if bool(matryoshka_cfg.get("matryoshka_enabled", False)):
        raw_dim = matryoshka_cfg.get("matryoshka_dim", 1024)
        v1024_dim = raw_dim if isinstance(raw_dim, int) else 1024

    fixed = 0
    failed = 0
    async with pg_pool.acquire() as conn, conn.transaction():
        # Same GUC-in-transaction contract as the scan block above; the
        # UPDATEs additionally pin tenant_id explicitly so a missing/lost
        # tenant context can never match (or, worse, clear flags on) another
        # tenant's rows.
        await conn.execute("SELECT set_config('app.current_tenant_id', $1, true)", tenant_id)
        for chunk in chunks:
            if chunk.embedding is None or _all_zero([chunk.embedding]):
                failed += 1
                continue
            vector_str = "[" + ",".join(str(v) for v in chunk.embedding) + "]"
            v1024: str | None = None
            if v1024_dim is not None:
                from ..rag.embedder import _slice_normalize

                v1024 = (
                    "["
                    + ",".join(str(v) for v in _slice_normalize(chunk.embedding, v1024_dim))
                    + "]"
                )
            await conn.execute(
                """
                UPDATE chunks_vectors
                SET vector = $2::vector,
                    vector_1024 = COALESCE($3::vector, chunks_vectors.vector_1024),
                    embedding_model = $4, dimensions = $5,
                    embedded_at = NOW(), degraded = FALSE
                WHERE chunk_id = $1::uuid AND tenant_id = $6
                """,
                chunk.chunk_id,
                vector_str,
                v1024,
                embedding_model,
                dimensions,
                tenant_id,
            )
            await conn.execute(
                "UPDATE chunks SET degraded = FALSE WHERE chunk_id = $1::uuid AND tenant_id = $2",
                chunk.chunk_id,
                tenant_id,
            )
            fixed += 1
    summary: dict[str, object] = {
        "status": "success",
        "scanned": len(chunks),
        "fixed": fixed,
        "failed": failed,
    }
    if embed_meta.get("embedding_mode") is not None:
        summary["embedding_mode"] = embed_meta["embedding_mode"]
    if embed_meta.get("embedding_note") is not None:
        summary["embedding_note"] = embed_meta["embedding_note"]
    if degraded and embed_err is not None:
        # The write path above already judged each vector per-chunk; this
        # flag reports the PRIMARY provider's health honestly (a fallback
        # repair still succeeds but the primary needs attention).
        summary["degraded"] = True
        summary["warning"] = embed_err
    return summary


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

    ``source_type`` is the DOCUMENT source type (it feeds the
    ``ingestion.strategies`` config mapping and content detection); use the
    explicit ``run_pipeline(strategy_name=...)`` override to pin a strategy.
    """
    if source_type not in {"code", "markdown", "text", "transcript"}:
        return {"status": "error", "message": f"Invalid source_type: {source_type}"}
    config = load_config_or_pass(config)
    return await run_pipeline(text, source_type, source, config, pg_pool, tenant_id, is_file=False)
