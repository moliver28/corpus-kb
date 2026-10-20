"""The labeling-function record and its apply contract (WP-2).

A labeling function (LF) is a frozen record: a name, a kind, the class
it votes for, that class's index, and its kind config. :meth:`LabelingFunction.apply`
scores one chunk and returns the class index, or ``MISSING`` (-1, the
``coding.aggregation`` sentinel) when the LF abstains. Abstention is a
first-class outcome: an LF that says nothing is evidence of nothing.

The built-in kinds and the JSON file schema live in ``lfs.py``. The
apply dispatch imports that module lazily, so ``protocol`` stays
import-light and the dependency points one way (protocol <- lfs).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field

from corpus_kb.coding.aggregation import MISSING

__all__ = ["MISSING", "LabelingFunction"]


@dataclass(frozen=True)
class LabelingFunction:
    """One authored LF: kind + label vote + config (the ``lfs.py`` schema).

    ``label_index`` is the position of ``label`` in the class vocabulary,
    so ``apply`` can return the integer matrix entry the aggregator
    expects.
    """

    name: str
    kind: str
    label: str
    label_index: int
    config: Mapping[str, object] = field(default_factory=dict)

    def apply(self, text: str, metadata: Mapping[str, object]) -> int:
        """Score one chunk: the class index, or MISSING on abstain.

        ``text`` is the chunk text; ``metadata`` is the chunk's stored
        jsonb metadata (web keys such as ``og:type`` live there). An
        unknown kind raises ``ValueError`` — the loader rejects it first,
        so this only fires on hand-built records.
        """
        from .kinds import applier_for  # lazy: keeps protocol import-light

        if applier_for(self.kind)(self.config, text, metadata):
            return self.label_index
        return MISSING
