"""The typed question intent for the analysis router (wave 2).

``QuestionIntent`` is the storable artifact of one routing decision: the
normalized question, the outcome, the chosen method, the six routing
axes (``None`` until a later stage assigns them), the ranked candidates,
the readings shown when the answer is ambiguous, and a short note. Every
routed answer keeps its intent next to the result, so choices stay
auditable. This module is pure data: no embedder, no I/O, no config.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field

from corpus_kb.analysis.method_register_routing import ROUTING_VOCAB_FIELDS

ROUTING_OUTCOMES = ("resolved", "ambiguous", "insufficient")


def _default_axes() -> dict[str, str | None]:
    """All six routing axes, unassigned."""
    return dict.fromkeys(ROUTING_VOCAB_FIELDS, None)


@dataclass
class QuestionIntent:
    """One routing decision, kept next to the result for audit.

    ``status`` is one of :data:`ROUTING_OUTCOMES`. ``method_id`` is set
    only when the status is ``resolved``. ``readings`` carries the top
    candidate restatements when the status is ``ambiguous``. ``note`` is
    a short token such as ``"degraded"``; it is ``None`` otherwise.
    """

    question: str
    status: str = "insufficient"
    confidence: float = 0.0
    method_id: str | None = None
    axes: dict[str, str | None] = field(default_factory=_default_axes)
    ranked_method_ids: list[str] = field(default_factory=list)
    readings: list[dict[str, str]] = field(default_factory=list)
    note: str | None = None

    def __post_init__(self) -> None:
        if self.status not in ROUTING_OUTCOMES:
            allowed = ", ".join(ROUTING_OUTCOMES)
            raise ValueError(f"status: {self.status!r} is not supported. Use one of: {allowed}.")

    def to_envelope(self) -> dict[str, object]:
        """The JSON-ready form. Field order is stable for snapshots.

        The axes always carry all six pinned keys: a partially built
        intent merges its axes over the defaults here.
        """
        envelope = asdict(self)
        envelope["axes"] = {**_default_axes(), **self.axes}
        return envelope
