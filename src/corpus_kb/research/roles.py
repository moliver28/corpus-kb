"""Speaker role mapping (todo-11 (d), v5 §2.1).

Heuristic: the moderator is the speaker with the highest question-mark ratio,
tie-broken by speaking first. Roles are stored with role_confirmed=FALSE —
a human confirms per file. Groups (3+ speakers) are NEVER auto-confirmed:
every role stays 'unknown' until review (QA scenario: unknown speaker labels
=> role=unknown + review event, no crash).
"""

from __future__ import annotations

from corpus_kb.domain.transcript import TurnSpec

GROUP_SIZE_FLOOR = 4


def map_roles(turns: list[TurnSpec]) -> str:
    """Assign roles in place; returns 'auto' | 'group_review' | 'no_roles'."""
    speakers = _speaker_stats(turns)
    if not speakers:
        return "no_roles"
    if len(speakers) >= GROUP_SIZE_FLOOR:
        for turn in turns:
            turn.role = "unknown"
            turn.is_codable = turn.role_in_exchange != "instruction"
        return "group_review"
    moderator = max(
        speakers.items(),
        key=lambda item: (item[1]["question_ratio"], -item[1]["first_seq"]),
    )[0]
    for turn in turns:
        turn.role = "moderator" if turn.speaker == moderator else "participant"
        turn.is_codable = turn.speaker != moderator
    return "auto"


def speaker_role_basis(turns: list[TurnSpec]) -> str:
    speakers = _speaker_stats(turns)
    if not speakers:
        return "no speakers recovered"
    if len(speakers) >= GROUP_SIZE_FLOOR:
        return f"group of {len(speakers)} speakers; human confirm required"
    moderator = max(
        speakers.items(),
        key=lambda item: (item[1]["question_ratio"], -item[1]["first_seq"]),
    )[0]
    stats = speakers[moderator]
    return (
        f"auto: {moderator} question_ratio={stats['question_ratio']:.2f}, "
        f"first utterance seq={stats['first_seq']}"
    )


def _speaker_stats(turns: list[TurnSpec]) -> dict[str, dict[str, float]]:
    stats: dict[str, dict[str, float]] = {}
    for turn in turns:
        entry = stats.setdefault(turn.speaker, {"n": 0, "questions": 0, "first_seq": turn.seq})
        entry["n"] += 1
        entry["questions"] += 1 if "?" in turn.text else 0
    for entry in stats.values():
        entry["question_ratio"] = entry["questions"] / entry["n"] if entry["n"] else 0.0
    return stats
