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
from types import ModuleType
from typing import Protocol, cast

import asyncpg
import numpy as np

from corpus_kb.errors import DependencyMissingError
from corpus_kb.storage.tenant_conn import tenant_connection

from .settings import AUTO_MIN_TOPIC_SIZE, UMAP_RANDOM_STATE, TopicsSettings

logger = logging.getLogger(__name__)

TOPICS_EXTRA_REMEDIATION = "pip install 'corpus-kb[topics]'"

# Bound on one build's working set: 20k chunks of 2560 halfvec floats.
MAX_EMBEDDINGS_PER_BUILD = 20000

# bertopic hands outliers the -1 topic id; they are honest non-membership.
OUTLIER_TOPIC = -1

# Keywords recorded per topic (the head of the c-TF-IDF ranking).
KEYWORDS_PER_TOPIC = 10


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

    def get_topic_info(self) -> TopicInfoFrame: ...

    def get_topic(self, topic_id: int) -> list[tuple[str, float]]: ...


class BertopicModule(Protocol):
    """The lazy ``bertopic`` module surface."""

    def BERTopic(
        self,
        *,
        min_topic_size: int,
        umap_model: object,
        calculate_probabilities: bool,
        verbose: bool,
    ) -> BERTopicModel: ...


class UmapModule(Protocol):
    """The lazy ``umap`` module surface."""

    def UMAP(
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
    """One chunk text plus its stored embedding (read-only view)."""

    chunk_id: str
    text: str
    vector: list[float]


@dataclass(frozen=True)
class TopicBuild:
    """One discovered topic, before reduction and before writing."""

    topic_index: int
    label: str
    size: int
    keywords: list[list[object]] = field(default_factory=list)


@dataclass(frozen=True)
class EngineResult:
    """The fit output: topics, per-chunk assignments, and the params record."""

    topics: list[TopicBuild]
    assignments: list[tuple[str, int, float]]
    params: dict[str, object]
    outlier_count: int


async def read_stored_embeddings(
    pg_pool: asyncpg.Pool, tenant_id: str, limit: int = MAX_EMBEDDINGS_PER_BUILD
) -> list[StoredChunk]:
    """Read chunk texts + stored embeddings for one tenant (tenant GUC).

    Stale rows (``embedding IS NULL`` after the migration-025 retype) are
    excluded: a NULL vector is not a topic signal. Ordering is by chunk id
    so the fit sees one stable sequence.
    """
    async with tenant_connection(pg_pool, tenant_id) as conn:
        rows = await conn.fetch(
            """
            SELECT cv.chunk_id::text AS chunk_id, c.text, cv.embedding::text AS embedding
            FROM chunks_vectors cv
            JOIN chunks c ON c.chunk_id = cv.chunk_id AND c.tenant_id = cv.tenant_id
            WHERE cv.tenant_id = $1::uuid AND cv.embedding IS NOT NULL
            ORDER BY cv.chunk_id
            LIMIT $2
            """,
            tenant_id,
            int(limit),
        )
    chunks: list[StoredChunk] = []
    for row in rows:
        chunks.append(
            StoredChunk(
                chunk_id=str(row["chunk_id"]),
                text=str(row["text"]),
                vector=_parse_halfvec_text(str(row["embedding"])),
            )
        )
    return chunks


def _parse_halfvec_text(raw: str) -> list[float]:
    """pgvector text form ``[0.1,0.2,...]`` is valid JSON."""
    parsed: object = json.loads(raw)
    if not isinstance(parsed, list):
        return []
    return [float(value) for value in parsed]


def build_topics(chunks: list[StoredChunk], settings: TopicsSettings) -> EngineResult:
    """Fit BERTopic over the stored vectors; deterministic, params recorded.

    Raises ``TopicDependencyError`` when the ``[topics]`` extra is absent.
    Fewer documents than the resolved ``min_topic_size`` is an honest
    empty result (the caller owns the status), never a crash.
    """
    bertopic_module = cast(BertopicModule, load_bertopic())
    umap_module = cast(UmapModule, load_umap())
    min_topic_size = settings.resolved_min_topic_size()
    params: dict[str, object] = {
        "method": settings.method,
        "min_topic_size_requested": settings.min_topic_size,
        "min_topic_size": min_topic_size,
        "auto_min_topic_size": AUTO_MIN_TOPIC_SIZE,
        "umap_random_state": UMAP_RANDOM_STATE,
        "calculate_probabilities": False,
        "embedding_source": "chunks_vectors.embedding",
        "documents": len(chunks),
    }
    model = bertopic_module.BERTopic(
        min_topic_size=min_topic_size,
        umap_model=_make_umap(umap_module),
        calculate_probabilities=False,
        verbose=False,
    )
    docs = [chunk.text for chunk in chunks]
    vectors = np.array([chunk.vector for chunk in chunks], dtype=np.float32)
    assigned, probabilities = model.fit_transform(docs, embeddings=vectors)
    return _collect(model, chunks, assigned, probabilities, params)


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
        topics=topics, assignments=assignments, params=params, outlier_count=outlier_count
    )
