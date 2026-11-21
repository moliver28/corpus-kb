"""MCP/HTTP/socket/CLI handler for survey open-end coding (register 31).

:func:`survey_openend_codes` groups the stored open-ended survey answers
per question, clusters each question's answers, and reports one code per
cluster with its answer count. The survey strategy stores ONE CHUNK PER
ANSWER (``ingestion/strategies/survey.py``): the chunk text is
``Q: {question}\\nA: {answer}`` and the metadata carries ``strategy``,
``question``, ``row``, and ``answer_column``. This handler reads that
shape — chunks without the survey metadata keys never enter a code.

Clustering reuses the ``clusters_inspect`` primitives (``_reduce`` and
``_cluster``). Labels come from the exemplar answer (the member nearest
the cluster centroid), trimmed. A question with fewer answers than the
cluster floor yields one code holding all its answers; noise members
form one extra "unclustered" code per question.

Honest outcomes: ``empty`` (no survey chunks in the tenant). A missing
``[classify]`` extra raises the typed dependency error. Floats never
enter this envelope; counts are integers.
"""

from __future__ import annotations

from typing import Any, cast

import asyncpg

from corpus_kb.errors import DependencyMissingError, ValidationError
from corpus_kb.labeling import engine
from corpus_kb.labeling.engine import StoredChunk

from ..analysis import vectors
from .cluster_tools import _cluster, _reduce
from .ingest_common import DEFAULT_TENANT_ID, load_config_or_pass

__all__ = ["survey_openend_codes"]

# Read bound (a guard, not a target).
MAX_SURVEY_CHUNKS = 50_000

# The cluster floor mirrors clusters_inspect's default.
MIN_CLUSTER_SIZE_DEFAULT = 5

# One code per question holds every answer below this floor.
SINGLE_CODE_FLOOR = 2

# Exemplar chunk ids reported per code.
EXEMPLAR_COUNT = 3

# The cluster label is the exemplar answer, trimmed to this width.
LABEL_MAX_CHARS = 80

_EMPTY_REASON = (
    "the tenant has no stored survey answers (no chunks carry the survey strategy metadata)."
)
_EMPTY_REMEDY = (
    "Ingest a survey CSV with a question column and answer columns first "
    "(the survey strategy), then retry."
)

EVIDENCE_NOTE = (
    "Embedding clusters with exemplar labels. This is not human coding. "
    "Check a sample of coded answers by hand before you use the counts."
)


class _SurveyDependencyError(DependencyMissingError):
    """The ``[classify]`` extra is not installed (sklearn clustering)."""

    remediation = (
        "The survey coding feature needs scikit-learn. Install the extra: "
        "pip install 'corpus-kb[classify]'."
    )


def _honest(status: str, reason: str, remedy: str, screened: int = 0) -> dict[str, object]:
    """The honest no-op envelope (reason + remedy named)."""
    return {
        "status": status,
        "reason": reason,
        "remedy": remedy,
        "codes": [],
        "screened_count": screened,
    }


def _survey_answer(chunk: StoredChunk) -> tuple[str, str] | None:
    """The (question, answer) pair for one survey chunk, else None."""
    metadata: dict[str, Any] = chunk.metadata or {}
    if metadata.get("strategy") != "survey" or "question" not in metadata:
        return None
    text = chunk.text
    marker = "\nA: "
    if not text.startswith("Q: ") or marker not in text:
        return None
    question = str(metadata["question"])
    answer = text.split(marker, 1)[1].strip()
    if not answer:
        return None
    return question, answer


async def _load_vectors(
    pg_pool: asyncpg.Pool, tenant_id: str, config: dict[str, object] | None
) -> vectors.VectorSpace:
    """The vector seam (tests substitute fakes here)."""
    return await vectors.load_tenant_vectors(pg_pool, tenant_id, config)


def _trim(text: str) -> str:
    """The cluster label: the exemplar answer, trimmed to the width cap."""
    text = " ".join(text.split())
    return text if len(text) <= LABEL_MAX_CHARS else text[: LABEL_MAX_CHARS - 1] + "\u2026"


def _min_cluster_floor(min_cluster_size: int | None) -> int:
    """Resolve and check the caller's cluster floor (default wins on None)."""
    if min_cluster_size is None:
        return MIN_CLUSTER_SIZE_DEFAULT
    if (
        isinstance(min_cluster_size, bool)
        or not isinstance(min_cluster_size, int)
        or min_cluster_size < 2
    ):
        raise ValidationError("min_cluster_size must be a whole number of 2 or more.")
    return min_cluster_size


def _select_answers(
    chunks: list[StoredChunk], chunk_ids: list[str] | None
) -> list[tuple[str, str, str]]:
    """The (question, chunk_id, answer) triples the requested chunks hold."""
    if chunk_ids is not None:
        wanted = {str(chunk_id) for chunk_id in chunk_ids}
        chunks = [chunk for chunk in chunks if chunk.chunk_id in wanted]
    answers: list[tuple[str, str, str]] = []  # (question, chunk_id, answer)
    for chunk in chunks:
        pair = _survey_answer(chunk)
        if pair is not None:
            answers.append((pair[0], chunk.chunk_id, pair[1]))
    return answers


def _group_by_vector(
    answers: list[tuple[str, str, str]], positions: dict[str, int]
) -> tuple[dict[str, list[tuple[str, str]]], int]:
    """Group the answers per question; count the ones without a vector."""
    grouped: dict[str, list[tuple[str, str]]] = {}
    excluded = 0
    for question, chunk_id, answer in answers:
        if chunk_id in positions:
            grouped.setdefault(question, []).append((chunk_id, answer))
        else:
            excluded += 1
    return grouped, excluded


async def survey_openend_codes(
    pg_pool: asyncpg.Pool,
    config: dict[str, object] | None = None,
    tenant_id: str = DEFAULT_TENANT_ID,
    chunk_ids: list[str] | None = None,
    min_cluster_size: int | None = None,
) -> dict[str, object]:
    """Code the tenant's open-ended survey answers per question.

    Returns ``{status, codes, screened_count}``. Each code row is
    ``{question, code_id, label, count, exemplar_chunk_ids}`` with rows
    ordered question asc, then count desc, then code_id asc. Answers
    without a stored vector are excluded and counted in ``params``.
    """
    effective_min = _min_cluster_floor(min_cluster_size)
    cfg = load_config_or_pass(config)
    chunks = await engine.read_chunks(pg_pool, tenant_id, MAX_SURVEY_CHUNKS)
    answers = _select_answers(chunks, chunk_ids)
    if not answers:
        return _honest("empty", _EMPTY_REASON, _EMPTY_REMEDY)
    space = await _load_vectors(pg_pool, tenant_id, cfg)
    positions = {chunk_id: index for index, chunk_id in enumerate(space.ids)}
    grouped, excluded = _group_by_vector(answers, positions)
    codes: list[dict[str, object]] = []
    reductions: set[str] = set()
    for question in sorted(grouped):
        members = grouped[question]
        if len(members) < max(SINGLE_CODE_FLOOR, effective_min):
            codes.append(_single_code(question, members))
            continue
        matrix = space.matrix[[positions[cid] for cid, _ in members]]
        try:
            reduced, reduction = _reduce(matrix)
            labels = _cluster(reduced, min(effective_min, len(members)))
        except DependencyMissingError:
            raise _SurveyDependencyError(_SurveyDependencyError.remediation) from None
        reductions.add(reduction)
        codes.extend(_cluster_codes(question, members, reduced, labels))
    codes.sort(
        key=lambda row: (
            str(row["question"]),
            -int(cast(int, row["count"])),
            str(row["code_id"]),
        )
    )
    return {
        "status": "success",
        "codes": codes,
        "screened_count": len(answers),
        "evidence_note": EVIDENCE_NOTE,
        "params": {
            "min_cluster_size": effective_min,
            "reduction": " and ".join(sorted(reductions)) or "none",
            "questions": len(grouped),
            "excluded_no_vector": excluded,
        },
    }


def _single_code(question: str, members: list[tuple[str, str]]) -> dict[str, object]:
    """One code below the cluster floor; the label is the first answer."""
    first = min(members, key=lambda member: member[0])
    return {
        "question": question,
        "code_id": "c-1",
        "label": _trim(first[1]),
        "count": len(members),
        "exemplar_chunk_ids": [member[0] for member in members[:EXEMPLAR_COUNT]],
    }


def _cluster_codes(
    question: str,
    members: list[tuple[str, str]],
    reduced: Any,
    labels: Any,
) -> list[dict[str, object]]:
    """Per-cluster code rows; noise members join one unclustered code."""
    clusters: dict[int, list[int]] = {}
    noise: list[int] = []
    for position, label in enumerate(labels.tolist()):
        if label < 0:
            noise.append(position)
        else:
            clusters.setdefault(int(label), []).append(position)
    ranked: list[tuple[int, list[int]]] = sorted(
        clusters.items(), key=lambda item: (-len(item[1]), item[0])
    )
    rows: list[dict[str, object]] = []
    for number, (_cluster_id, positions_) in enumerate(ranked, start=1):
        centroid = reduced[positions_].mean(axis=0)
        ordered = sorted(
            positions_,
            key=lambda p: (float(abs(reduced[p] - centroid).sum()), members[p][0]),
        )
        rows.append(
            {
                "question": question,
                "code_id": f"c-{number}",
                "label": _trim(members[ordered[0]][1]),
                "count": len(positions_),
                "exemplar_chunk_ids": [members[p][0] for p in ordered[:EXEMPLAR_COUNT]],
            }
        )
    if noise:
        ordered = sorted(noise, key=lambda p: members[p][0])
        rows.append(
            {
                "question": question,
                "code_id": f"c-{len(ranked) + 1}",
                "label": "unclustered answers",
                "count": len(noise),
                "exemplar_chunk_ids": [members[p][0] for p in ordered[:EXEMPLAR_COUNT]],
            }
        )
    return rows
