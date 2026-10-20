"""The offline BERTopic engine over STORED chunk embeddings (Wave 4).

Design contract:

- CONSUMES, never produces, vectors: the fit reads
  ``chunks_vectors.embedding`` (halfvec(2560)) joined to ``chunks`` for
  the document texts. Nothing here calls an embedder.
- LAZY DEPENDENCY: bertopic is imported inside :func:`load_bertopic` so
  the extras-less runtime (and CI) never touches it. A missing extra is
  a typed ``DependencyMissingError`` with the one-line remedy. The
  Protocols below pin the small model surface the engine uses, so tests
  can drive a fake through the same seam (monkeypatched loaders).
- DETERMINISM: the UMAP random state is the pinned constant
  ``UMAP_RANDOM_STATE`` (42, the repo-wide research pin) and every model
  parameter is recorded in the result's ``params`` mapping.
- CORPUS SCALE (WP-0): the read pages through ALL embedded chunks for
  the tenant in bounded batches; the fit runs on a seeded sample
  (``FIT_SAMPLE_SEED``) and the remainder is assigned through
  ``transform`` (UMAP transform + hdbscan approximate_predict), which
  is deterministic given the fitted state. One fit per build, never two.
- "auto": ``min_topic_size: "auto"`` passes BERTopic's own default
  (``AUTO_MIN_TOPIC_SIZE``) and the resolved value is reported.

The heavy ``fit`` is synchronous CPU work by nature; the async op layer
calls it directly (an offline operator command, not a request path).
"""

from __future__ import annotations

import importlib
import json
import logging
from dataclasses import dataclass, field
from datetime import datetime
from types import ModuleType
from typing import Protocol, cast

import asyncpg
import numpy as np

from corpus_kb.errors import DependencyMissingError
from corpus_kb.storage.tenant_conn import tenant_connection

from .settings import AUTO_MIN_TOPIC_SIZE, UMAP_RANDOM_STATE, TopicsSettings

logger = logging.getLogger(__name__)

TOPICS_EXTRA_REMEDIATION = "pip install 'corpus-kb[topics]'"

# One read batch and the default fit-sample cap: 20k chunks of 2560
# halfvec floats per batch (WP-0: the read pages the whole corpus with
# this bound; the fit samples this many vectors unless
# ``analysis.topics.max_embeddings`` lowers it).
MAX_EMBEDDINGS_PER_BUILD = 20000

# The seeded-sample pin (the UMAP_RANDOM_STATE convention: a code
# constant, recorded in every build result, never a knob).
FIT_SAMPLE_SEED = 42

# bertopic hands outliers the -1 topic id; they are honest non-membership.
OUTLIER_TOPIC = -1

# Keywords recorded per topic (the head of the c-TF-IDF ranking).
KEYWORDS_PER_TOPIC = 10

# Keyset-paginated read (WP-0): one bounded batch per fetch, ordered by
# the chunk-id text so the pagination cursor cannot loop or skip. The
# documents join carries the date provenance the temporal build needs.
_READ_SQL = """
SELECT cv.chunk_id::text AS chunk_id, c.text, cv.embedding::text AS embedding,
       c.doc_id::text AS doc_id, d.document_date AS document_date,
       c.source_timestamp AS source_timestamp
FROM chunks_vectors cv
JOIN chunks c ON c.chunk_id = cv.chunk_id AND c.tenant_id = cv.tenant_id
JOIN documents d ON d.doc_id = c.doc_id AND d.tenant_id = c.tenant_id
WHERE cv.tenant_id = $1::uuid AND cv.embedding IS NOT NULL
  AND cv.chunk_id::text > $2::text
ORDER BY cv.chunk_id::text
LIMIT $3
"""


class TopicDependencyError(DependencyMissingError):
    """The ``[topics]`` extra is not installed."""

    remediation = TOPICS_EXTRA_REMEDIATION


class TopicInfoFrame(Protocol):
    """The one DataFrame method the engine reads (pandas-compatible)."""

    def to_dict(self, orient: str) -> list[dict[str, object]]: ...


class BERTopicModel(Protocol):
    """The fitted-model surface the engine touches."""

    def fit_transform(
        self, documents: list[str], embeddings: object
    ) -> tuple[list[object], list[object] | None]: ...

    def transform(
        self, documents: list[str], embeddings: object
    ) -> tuple[list[object], list[object] | None]: ...

    # Keyword names mirror the real ``BERTopic.topics_over_time``.
    def topics_over_time(
        self,
        docs: list[str],
        timestamps: list[object],
        *,
        topics: list[int],
        nr_bins: int,
        evolution_tuning: bool,
    ) -> TopicInfoFrame: ...

    def get_topic_info(self) -> TopicInfoFrame: ...

    def get_topic(self, topic_id: int) -> list[tuple[str, float]]: ...


class BertopicModule(Protocol):
    """The lazy ``bertopic`` module surface."""

    def BERTopic(  # noqa: N802 - mirrors the real library's class name
        self,
        *,
        min_topic_size: int,
        umap_model: object,
        calculate_probabilities: bool,
        verbose: bool,
    ) -> BERTopicModel: ...


class UmapModule(Protocol):
    """The lazy ``umap`` module surface."""

    def UMAP(  # noqa: N802 - mirrors the real library's class name
        self, *, random_state: int, n_components: int, min_dist: float, metric: str
    ) -> object: ...


def _import_module(name: str) -> ModuleType:
    """The one import seam (tests substitute a ModuleNotFoundError here)."""
    return importlib.import_module(name)


def load_bertopic() -> ModuleType:
    """Import bertopic lazily; a missing extra is a typed error."""
    try:
        return _import_module("bertopic")
    except ModuleNotFoundError as exc:
        raise TopicDependencyError(
            "The topics feature needs the bertopic package (the [topics] extra)."
        ) from exc


def load_umap() -> ModuleType:
    """Import umap lazily (a bertopic dependency, loaded through the same seam)."""
    try:
        return _import_module("umap")
    except ModuleNotFoundError as exc:
        raise TopicDependencyError(
            "The topics feature needs the umap-learn package (with bertopic)."
        ) from exc


@dataclass(frozen=True)
class StoredChunk:
    """One chunk text plus its stored embedding (read-only view).

    ``document_date`` (migration 027) is the document-level date; the
    chunk's own ``source_timestamp`` is the fallback (WP-4).
    """

    chunk_id: str
    text: str
    vector: list[float]
    doc_id: str = ""
    document_date: datetime | None = None
    source_timestamp: datetime | None = None


@dataclass(frozen=True)
class TopicBuild:
    """One discovered topic, before reduction and before writing."""

    topic_index: int
    label: str
    size: int
    keywords: list[list[object]] = field(default_factory=list)


@dataclass(frozen=True)
class EngineResult:
    """The fit output: topics, per-chunk assignments, and the params record.

    ``doc_topics`` is the topic id for EVERY input chunk in input order
    (outliers included as ``OUTLIER_TOPIC``). The temporal build (WP-4)
    needs it: its docs, timestamps and topics must align one to one.
    """

    topics: list[TopicBuild]
    assignments: list[tuple[str, int, float]]
    params: dict[str, object]
    outlier_count: int
    doc_topics: list[int] = field(default_factory=list)


async def read_stored_embeddings(
    pg_pool: asyncpg.Pool, tenant_id: str, limit: int | None = None
) -> list[StoredChunk]:
    """Read the tenant's embedded chunks (tenant GUC), batched.

    ``limit=None`` (the default) reads EVERY embedded chunk; an int caps
    the total across batches. Each fetch is bounded by
    ``MAX_EMBEDDINGS_PER_BUILD`` rows (keyset pagination, so one stable
    pass over any corpus size). Stale rows (``embedding IS NULL`` after
    the migration-025 retype) are excluded: a NULL vector is not a topic
    signal. Ordering is by chunk id so the fit sees one stable sequence.
    """
    chunks: list[StoredChunk] = []
    async with tenant_connection(pg_pool, tenant_id) as conn:
        after = ""  # every chunk-id text is greater than the empty string
        while limit is None or len(chunks) < limit:
            batch = MAX_EMBEDDINGS_PER_BUILD
            if limit is not None:
                batch = min(MAX_EMBEDDINGS_PER_BUILD, limit - len(chunks))
            rows = await conn.fetch(_READ_SQL, tenant_id, after, batch)
            if not rows:
                break
            chunks.extend(_stored_chunk(row) for row in rows)
            after = str(rows[-1]["chunk_id"])
    return chunks


def _stored_chunk(row: asyncpg.Record) -> StoredChunk:
    """One row -> the frozen view (uuid -> text, timestamps kept)."""
    return StoredChunk(
        chunk_id=str(row["chunk_id"]),
        text=str(row["text"]),
        vector=_parse_halfvec_text(str(row["embedding"])),
        doc_id=str(row["doc_id"]),
        document_date=_as_datetime(row["document_date"]),
        source_timestamp=_as_datetime(row["source_timestamp"]),
    )


def _as_datetime(value: object) -> datetime | None:
    """asyncpg hands timestamptz back as datetime; anything else is None."""
    return value if isinstance(value, datetime) else None


def _parse_halfvec_text(raw: str) -> list[float]:
    """pgvector text form ``[0.1,0.2,...]`` is valid JSON."""
    parsed: object = json.loads(raw)
    if not isinstance(parsed, list):
        return []
    return [float(value) for value in parsed]


def build_topics(
    chunks: list[StoredChunk],
    settings: TopicsSettings,
    max_embeddings: int | None = None,
) -> EngineResult:
    """Fit BERTopic over the stored vectors; deterministic, params recorded.

    Raises ``TopicDependencyError`` when the ``[topics]`` extra is absent.
    Fewer documents than the resolved ``min_topic_size`` is an honest
    empty result (the caller owns the status), never a crash.
    """
    result, _model = build_topics_with_model(chunks, settings, max_embeddings)
    return result


def build_topics_with_model(
    chunks: list[StoredChunk],
    settings: TopicsSettings,
    max_embeddings: int | None = None,
) -> tuple[EngineResult, BERTopicModel]:
    """Fit once and return the result AND the fitted model (the WP-4 seam).

    Corpus-scale path (WP-0): the fit consumes a seeded sample of at
    most ``max_embeddings`` vectors (``analysis.topics.max_embeddings``,
    else ``MAX_EMBEDDINGS_PER_BUILD``); the remainder is assigned by
    ``transform``. Below the fit floor is the caller's honest
    ``insufficient_data`` gate, never this function's business.
    """
    bertopic_module = cast(BertopicModule, load_bertopic())
    umap_module = cast(UmapModule, load_umap())
    min_topic_size = settings.resolved_min_topic_size()
    sample_cap = max_embeddings if max_embeddings is not None else MAX_EMBEDDINGS_PER_BUILD
    sample_index = _sample_index(len(chunks), sample_cap)
    rest_index = [i for i in range(len(chunks)) if i not in set(sample_index)]
    params: dict[str, object] = {
        "method": settings.method,
        "min_topic_size_requested": settings.min_topic_size,
        "min_topic_size": min_topic_size,
        "auto_min_topic_size": AUTO_MIN_TOPIC_SIZE,
        "umap_random_state": UMAP_RANDOM_STATE,
        "calculate_probabilities": False,
        "embedding_source": "chunks_vectors.embedding",
        "documents": len(chunks),
        "fit_sample_size": len(sample_index),
        "fit_seed": FIT_SAMPLE_SEED,
        "assignment_mode": "fit+transform" if rest_index else "fit",
        "total_chunks": len(chunks),
    }
    model = bertopic_module.BERTopic(
        min_topic_size=min_topic_size,
        umap_model=_make_umap(umap_module),
        calculate_probabilities=False,
        verbose=False,
    )
    sample = [chunks[i] for i in sample_index]
    fit_docs = [chunk.text for chunk in sample]
    fit_vectors = np.array([chunk.vector for chunk in sample], dtype=np.float32)
    fit_topics, fit_probabilities = model.fit_transform(fit_docs, embeddings=fit_vectors)
    rest_topics: list[object] = []
    rest_probabilities: list[object] = []
    if rest_index:
        rest_docs = [chunks[i].text for i in rest_index]
        rest_vectors = np.array([chunks[i].vector for i in rest_index], dtype=np.float32)
        rest_topics, rest_raw_probabilities = model.transform(rest_docs, embeddings=rest_vectors)
        if rest_raw_probabilities is not None:
            rest_probabilities = list(rest_raw_probabilities)
    assigned = _merge_values(
        sample_index, list(fit_topics), rest_index, rest_topics, len(chunks), OUTLIER_TOPIC
    )
    probabilities = _merge_values(
        sample_index,
        list(fit_probabilities) if fit_probabilities is not None else [],
        rest_index,
        rest_probabilities,
        len(chunks),
        0.0,
    )
    return _collect(model, chunks, assigned, probabilities, params), model


def _sample_index(total: int, cap: int) -> list[int]:
    """The seeded fit sample (sorted: one stable input order)."""
    if total <= cap:
        return list(range(total))
    rng = np.random.default_rng(FIT_SAMPLE_SEED)
    return sorted(int(index) for index in rng.choice(total, size=cap, replace=False))


def _merge_values(
    sample_index: list[int],
    sample_values: list[object],
    rest_index: list[int],
    rest_values: list[object],
    total: int,
    fill: object,
) -> list[object]:
    """Scatter sample/transform values back into input order.

    ``fill`` marks a position no model output reached: ``OUTLIER_TOPIC``
    for topic ids (honest non-membership), 0.0 for probabilities (the
    missing-probability default).
    """
    merged: list[object] = [fill] * total
    for position, value in enumerate(sample_values):
        merged[sample_index[position]] = value
    for position, value in enumerate(rest_values):
        merged[rest_index[position]] = value
    return merged


def _make_umap(umap_module: UmapModule) -> object:
    """The deterministic UMAP instance (random state pinned at 42)."""
    return umap_module.UMAP(
        random_state=UMAP_RANDOM_STATE,
        n_components=5,
        min_dist=0.0,
        metric="cosine",
    )


def _collect(
    model: BERTopicModel,
    chunks: list[StoredChunk],
    assigned: list[object],
    probabilities: list[object] | None,
    params: dict[str, object],
) -> EngineResult:
    """Extract topics/keywords/assignments from the fitted model."""
    topics: list[TopicBuild] = []
    outlier_count = 0
    for row in model.get_topic_info().to_dict("records"):
        topic_index = int(cast("int", row["Topic"]))
        count = int(cast("int", row["Count"]))
        if topic_index == OUTLIER_TOPIC:
            outlier_count = count
            continue
        topics.append(
            TopicBuild(
                topic_index=topic_index,
                label=str(row["Name"]),
                size=count,
                keywords=[
                    [str(word), float(score)]
                    for word, score in model.get_topic(topic_index)[:KEYWORDS_PER_TOPIC]
                ],
            )
        )
    topics.sort(key=lambda t: t.topic_index)
    assignments: list[tuple[str, int, float]] = []
    prob_list = list(probabilities) if probabilities is not None else []
    for position, raw_topic in enumerate(assigned):
        topic_index = int(cast("int", raw_topic))
        if topic_index == OUTLIER_TOPIC:
            continue
        raw_prob = float(cast("float", prob_list[position])) if position < len(prob_list) else 0.0
        assignments.append((chunks[position].chunk_id, topic_index, min(max(raw_prob, 0.0), 1.0)))
    params["probabilities_note"] = "approximate (calculate_probabilities=false)"
    params["outliers"] = outlier_count
    return EngineResult(
        topics=topics,
        assignments=assignments,
        params=params,
        outlier_count=outlier_count,
        doc_topics=[int(cast("int", raw)) for raw in assigned],
    )
