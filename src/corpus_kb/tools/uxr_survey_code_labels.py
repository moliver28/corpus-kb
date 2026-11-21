"""Code-row builders for survey open-end coding (register 31).

Split from ``uxr_survey_codes.py`` so both files stay under the
250-line soft limit. ``uxr_survey_codes`` groups each question's
answers; this module turns one group into code rows. The cluster label
is the exemplar answer (the member nearest the centroid), trimmed to
the width cap. Noise members join one "unclustered answers" code.
"""

from __future__ import annotations

from typing import Any

__all__ = ["EXEMPLAR_COUNT", "LABEL_MAX_CHARS", "cluster_code_rows", "single_code", "trim"]

# Exemplar chunk ids reported per code.
EXEMPLAR_COUNT = 3

# The cluster label is the exemplar answer, trimmed to this width.
LABEL_MAX_CHARS = 80


def trim(text: str) -> str:
    """The cluster label: the exemplar answer, trimmed to the width cap."""
    text = " ".join(text.split())
    return text if len(text) <= LABEL_MAX_CHARS else text[: LABEL_MAX_CHARS - 1] + "\u2026"


def single_code(question: str, members: list[tuple[str, str]]) -> dict[str, object]:
    """One code below the cluster floor; the label is the first answer."""
    first = min(members, key=lambda member: member[0])
    return {
        "question": question,
        "code_id": "c-1",
        "label": trim(first[1]),
        "count": len(members),
        "exemplar_chunk_ids": [member[0] for member in members[:EXEMPLAR_COUNT]],
    }


def cluster_code_rows(
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
                "label": trim(members[ordered[0]][1]),
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
