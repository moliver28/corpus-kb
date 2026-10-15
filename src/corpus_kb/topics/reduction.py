"""LLM topic label reduction: labels + merges, fixture-first, fail-open.

Mirrors ``rag/contextualizer.py``: a SHA256-keyed fixture cache, a
``live_fallback`` gate, a hard client timeout, and ONE honesty rule —
the reduction NEVER raises into the run. Any LLM failure (down server,
missing model, unparsable answer) keeps BERTopic's own c-TF-IDF labels
and records the note in the result.

The LLM contract: per topic a short human label, plus optional
near-duplicate merges (``absorbed`` topic folds into ``survivor``).
Merges are validated against the discovered topic ids; unknown ids and
self-merges are dropped, so a hallucinating model cannot corrupt the
build. ``apply_reduction`` is the pure remapper (no I/O), unit-tested on
its own.
"""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass, field
from pathlib import Path

from ollama import Client

from .engine import EngineResult, OUTLIER_TOPIC, TopicBuild
from .settings import TopicsSettings

logger = logging.getLogger(__name__)

# Bound on every generate call (seconds) — the contextualizer convention:
# the ollama client otherwise waits forever on a wedged server.
REDUCTION_TIMEOUT_SECONDS = 120.0

_LABELS_KEY = "labels"
_MERGE_KEY = "merge"
_MAX_LABEL_WORDS = 6

_PROMPT_TEMPLATE = (
    "You name topic clusters for a knowledge base.\n"
    "For every topic, write a short label of at most 6 words.\n"
    "You may merge a near-duplicate topic into a sibling topic.\n"
    'Answer with JSON only: {{"labels": {{"<topic>": "<label>"}}, '
    '"merge": [["<absorbed>", "<survivor>"]]}}\n'
    "Merge is optional and may be empty.\n"
    "Use the topic numbers as JSON keys.\n\n"
    "Topics:\n{topics}"
)


@dataclass(frozen=True)
class ReductionResult:
    """What the reduction step decided (status + the note when degraded)."""

    status: str  # applied | fixture | skipped | failed
    labels: dict[int, str] = field(default_factory=dict)
    merge_map: dict[int, int] = field(default_factory=dict)
    note: str | None = None


def _render_topics(topics: list[TopicBuild]) -> str:
    lines = []
    for topic in topics:
        words = ", ".join(str(word) for word, _score in topic.keywords[:8])
        lines.append(f"- topic {topic.topic_index} ({topic.size} chunks): '{topic.label}'; words: {words}")
    return "\n".join(lines)


def _fixture_key(model: str, topics: list[TopicBuild]) -> str:
    """SHA256 over the model + the canonical topic list (contextualizer rule)."""
    payload = json.dumps(
        [[t.topic_index, t.label, t.size, t.keywords] for t in topics],
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(f"{model}\x00{payload}".encode("utf-8")).hexdigest()


class TopicReducer:
    """Fixture-first LLM reduction over the discovered topics."""

    def __init__(self, settings: TopicsSettings) -> None:
        self._settings = settings
        fixture_dir = settings.fixture_dir
        self._fixture_dir = Path(fixture_dir) if fixture_dir else None
        self._client = Client(host=settings.llm_base_url, timeout=REDUCTION_TIMEOUT_SECONDS)

    def reduce(self, topics: list[TopicBuild]) -> ReductionResult:
        """Label (and maybe merge) the topics; never raises."""
        if not topics:
            return ReductionResult(status="skipped", note="no topics to reduce")
        known = {topic.topic_index for topic in topics}
        key = _fixture_key(self._settings.llm, topics)
        fixture = self._read_fixture(key)
        if fixture is not None:
            parsed = _parse_answer(fixture, known)
            if parsed is not None:
                labels, merge_map = parsed
                return ReductionResult(
                    status="fixture", labels=labels, merge_map=merge_map
                )
        if not self._settings.live_fallback:
            return ReductionResult(
                status="skipped",
                note="no recorded fixture and live_fallback is false; c-TF-IDF labels kept",
            )
        try:
            answer = self._generate(topics)
        except Exception as exc:  # never raise into the run (contextualizer rule)
            logger.warning("Topic reduction unavailable: %s; c-TF-IDF labels kept.", exc)
            return ReductionResult(status="failed", note=f"{type(exc).__name__}: {exc}")
        parsed = _parse_answer(answer, known)
        if parsed is None:
            return ReductionResult(
                status="failed", note="the model answer was not valid reduction JSON"
            )
        labels, merge_map = parsed
        return ReductionResult(status="applied", labels=labels, merge_map=merge_map)

    def _read_fixture(self, key: str) -> str | None:
        if self._fixture_dir is None:
            return None
        path = self._fixture_dir / f"{key}.json"
        if not path.exists():
            return None
        return path.read_text(encoding="utf-8")

    def _generate(self, topics: list[TopicBuild]) -> str:
        response = self._client.generate(
            model=self._settings.llm,
            prompt=_PROMPT_TEMPLATE.format(topics=_render_topics(topics)),
            options={"temperature": 0.0},
        )
        text = response.get("response", "") if isinstance(response, dict) else str(response)
        return text.strip()


def _parse_answer(
    raw: str, known: set[int]
) -> tuple[dict[int, str], dict[int, int]] | None:
    """Parse the model answer into validated ``(labels, merge_map)``.

    Returns None when the answer is not the contracted JSON shape; the
    caller degrades to the c-TF-IDF labels. Unknown topic ids, self-merges
    and non-string labels are dropped per-entry.
    """
    text = raw.strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.startswith("json"):
            text = text[4:]
    parsed: object = None
    try:
        parsed = json.loads(text)
    except ValueError:
        return None
    if not isinstance(parsed, dict):
        return None
    return (_valid_labels(parsed.get(_LABELS_KEY), known), _valid_merges(parsed.get(_MERGE_KEY), known))


def _valid_labels(raw: object, valid: set[int]) -> dict[int, str]:
    if not isinstance(raw, dict):
        return {}
    labels: dict[int, str] = {}
    for key, value in raw.items():
        try:
            index = int(str(key))
        except ValueError:
            continue
        if index in valid and isinstance(value, str) and value.strip():
            labels[index] = value.strip()
    return labels


def _valid_merges(raw: object, valid: set[int]) -> dict[int, int]:
    """``[[absorbed, survivor], ...]`` -> absorbed -> survivor (validated)."""
    if not isinstance(raw, list):
        return {}
    merge_map: dict[int, int] = {}
    for pair in raw:
        if not isinstance(pair, (list, tuple)) or len(pair) != 2:
            continue
        try:
            absorbed, survivor = int(str(pair[0])), int(str(pair[1]))
        except ValueError:
            continue
        if absorbed == survivor or absorbed not in valid or survivor not in valid:
            continue
        merge_map[absorbed] = survivor
    return merge_map


def apply_reduction(engine_result: EngineResult, reduction: ReductionResult) -> EngineResult:
    """Remap topics/assignments through the validated reduction (pure).

    Merged topics fold into their survivor transitively; survivor sizes
    are recounted from the remapped assignments (the honest count), and
    reduced labels land in ``reduced_label`` space via ``labels``.
    """
    if not reduction.labels and not reduction.merge_map:
        return engine_result
    survivor_of = _resolve_survivors(reduction.merge_map)
    by_index = {topic.topic_index: topic for topic in engine_result.topics}
    surviving_topics = [
        topic for topic in engine_result.topics if topic.topic_index not in survivor_of
    ]
    new_assignments: list[tuple[str, int, float]] = []
    for chunk_id, topic_index, probability in engine_result.assignments:
        new_assignments.append(
            (chunk_id, survivor_of.get(topic_index, topic_index), probability)
        )
    recounted = _recount_sizes(surviving_topics, new_assignments)
    rebuilt = [
        TopicBuild(
            topic_index=topic.topic_index,
            label=topic.label,
            size=recounted.get(topic.topic_index, topic.size),
            keywords=topic.keywords,
        )
        for topic in surviving_topics
    ]
    return EngineResult(
        topics=rebuilt,
        assignments=new_assignments,
        params={
            **engine_result.params,
            "reduction_status": reduction.status,
            "merged_topics": sorted(survivor_of),
        },
        outlier_count=engine_result.outlier_count,
    )


def _resolve_survivors(merge_map: dict[int, int]) -> dict[int, int]:
    """Absorbed -> FINAL survivor, chains resolved (a -> b, b -> c: a -> c).

    A full cycle (0 -> 1, 1 -> 0) has no survivor; the pair is dropped so
    a hostile model answer can never delete every topic.
    """
    survivor_of: dict[int, int] = {}
    for absorbed, survivor in merge_map.items():
        target = survivor
        seen = {absorbed}
        while target in merge_map and target not in seen:
            seen.add(target)
            target = merge_map[target]
        if target != absorbed:
            survivor_of[absorbed] = target
    return survivor_of


def _recount_sizes(topics: list[TopicBuild], assignments: list[tuple[str, int, float]]) -> dict[int, int]:
    counts = {topic.topic_index: 0 for topic in topics}
    for _chunk_id, topic_index, _probability in assignments:
        if topic_index in counts and topic_index != OUTLIER_TOPIC:
            counts[topic_index] += 1
    return counts
